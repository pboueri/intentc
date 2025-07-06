package intent

import (
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

type TargetInfo struct {
	Name           string
	Intent         *IntentFile
	ValidationFiles []string
	LastModified   time.Time
	CacheKey       string
}

type TargetRegistry struct {
	projectRoot string
	targets     map[string]*TargetInfo
	aliases     map[string][]string
	cache       map[string]*cacheEntry
	cacheMutex  sync.RWMutex
	parser      *Parser
}

type cacheEntry struct {
	intent       *IntentFile
	lastModified time.Time
	expires      time.Time
}

const cacheDuration = 5 * time.Minute

func NewTargetRegistry(projectRoot string) *TargetRegistry {
	return &TargetRegistry{
		projectRoot: projectRoot,
		targets:     make(map[string]*TargetInfo),
		aliases:     make(map[string][]string),
		cache:       make(map[string]*cacheEntry),
		parser:      NewParser(),
	}
}

func (r *TargetRegistry) LoadTargets() error {
	// Clear existing targets
	r.targets = make(map[string]*TargetInfo)
	
	// Discover all intent files
	intents, err := r.parser.DiscoverIntentFiles(r.projectRoot)
	if err != nil {
		return fmt.Errorf("failed to discover intent files: %w", err)
	}
	
	// Register each intent as a target
	for _, intent := range intents {
		if err := r.RegisterTarget(intent); err != nil {
			return fmt.Errorf("failed to register target %s: %w", intent.Name, err)
		}
	}
	
	// Set up default aliases
	r.setupDefaultAliases()
	
	return nil
}

func (r *TargetRegistry) RegisterTarget(intent *IntentFile) error {
	// Get file modification time
	stat, err := os.Stat(intent.Path)
	if err != nil {
		return fmt.Errorf("failed to stat intent file: %w", err)
	}
	
	// Find validation files in the same directory
	dir := filepath.Dir(intent.Path)
	validationFiles, err := r.findValidationFiles(dir)
	if err != nil {
		return fmt.Errorf("failed to find validation files: %w", err)
	}
	
	target := &TargetInfo{
		Name:            intent.Name,
		Intent:          intent,
		ValidationFiles: validationFiles,
		LastModified:    stat.ModTime(),
		CacheKey:        r.generateCacheKey(intent.Path),
	}
	
	r.targets[intent.Name] = target
	
	// Also register by target names defined in the intent
	for _, t := range intent.Targets {
		if t.Name != "" && t.Name != intent.Name {
			r.targets[t.Name] = target
		}
	}
	
	return nil
}

func (r *TargetRegistry) findValidationFiles(dir string) ([]string, error) {
	var validationFiles []string
	
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	
	for _, entry := range entries {
		if !entry.IsDir() && filepath.Ext(entry.Name()) == ".icv" {
			validationFiles = append(validationFiles, filepath.Join(dir, entry.Name()))
		}
	}
	
	return validationFiles, nil
}

func (r *TargetRegistry) GetTarget(name string) (*TargetInfo, bool) {
	target, exists := r.targets[name]
	return target, exists
}

func (r *TargetRegistry) GetAllTargets() []*TargetInfo {
	var targets []*TargetInfo
	seen := make(map[string]bool)
	
	for _, target := range r.targets {
		if !seen[target.Name] {
			targets = append(targets, target)
			seen[target.Name] = true
		}
	}
	
	return targets
}

func (r *TargetRegistry) GetCachedIntent(path string) (*IntentFile, bool) {
	r.cacheMutex.RLock()
	defer r.cacheMutex.RUnlock()
	
	entry, exists := r.cache[path]
	if !exists {
		return nil, false
	}
	
	// Check if cache entry has expired
	if time.Now().After(entry.expires) {
		return nil, false
	}
	
	// Check if file has been modified since cache entry
	stat, err := os.Stat(path)
	if err != nil || stat.ModTime().After(entry.lastModified) {
		return nil, false
	}
	
	return entry.intent, true
}

func (r *TargetRegistry) CacheIntent(path string, intent *IntentFile) {
	r.cacheMutex.Lock()
	defer r.cacheMutex.Unlock()
	
	stat, err := os.Stat(path)
	if err != nil {
		return
	}
	
	r.cache[path] = &cacheEntry{
		intent:       intent,
		lastModified: stat.ModTime(),
		expires:      time.Now().Add(cacheDuration),
	}
}

func (r *TargetRegistry) ClearCache() {
	r.cacheMutex.Lock()
	defer r.cacheMutex.Unlock()
	
	r.cache = make(map[string]*cacheEntry)
}

func (r *TargetRegistry) InvalidateCache(path string) {
	r.cacheMutex.Lock()
	defer r.cacheMutex.Unlock()
	
	delete(r.cache, path)
}

func (r *TargetRegistry) generateCacheKey(path string) string {
	// Generate a cache key based on the file path and modification time
	stat, err := os.Stat(path)
	if err != nil {
		return path
	}
	
	return fmt.Sprintf("%s-%d", path, stat.ModTime().Unix())
}

func (r *TargetRegistry) setupDefaultAliases() {
	// All targets alias
	var allTargets []string
	for name := range r.targets {
		allTargets = append(allTargets, name)
	}
	if len(allTargets) > 0 {
		r.aliases["all"] = allTargets
	}
	
	// Project targets alias
	var projectTargets []string
	for _, target := range r.targets {
		if target.Intent != nil && target.Intent.Type == IntentTypeProject {
			projectTargets = append(projectTargets, target.Name)
		}
	}
	if len(projectTargets) > 0 {
		r.aliases["project"] = projectTargets
	}
	
	// Feature targets alias
	var featureTargets []string
	for _, target := range r.targets {
		if target.Intent != nil && target.Intent.Type == IntentTypeFeature {
			featureTargets = append(featureTargets, target.Name)
		}
	}
	if len(featureTargets) > 0 {
		r.aliases["features"] = featureTargets
	}
}

func (r *TargetRegistry) AddAlias(alias string, targets []string) {
	r.aliases[alias] = targets
}

func (r *TargetRegistry) GetAlias(alias string) ([]string, bool) {
	targets, exists := r.aliases[alias]
	return targets, exists
}

func (r *TargetRegistry) RefreshTarget(name string) error {
	target, exists := r.targets[name]
	if !exists {
		return fmt.Errorf("target %s not found", name)
	}
	
	if target.Intent == nil {
		return fmt.Errorf("target %s has no associated intent", name)
	}
	
	// Invalidate cache
	r.InvalidateCache(target.Intent.Path)
	
	// Re-parse the intent file
	intent, err := r.parser.ParseIntentFile(target.Intent.Path)
	if err != nil {
		return fmt.Errorf("failed to parse intent file: %w", err)
	}
	
	// Override the name with the directory name (consistent with discoverInDirectory)
	intent.Name = filepath.Base(filepath.Dir(target.Intent.Path))
	
	// Update the target
	target.Intent = intent
	
	// Update modification time
	stat, err := os.Stat(intent.Path)
	if err == nil {
		target.LastModified = stat.ModTime()
		target.CacheKey = r.generateCacheKey(intent.Path)
	}
	
	// Cache the parsed intent
	r.CacheIntent(intent.Path, intent)
	
	return nil
}

func (r *TargetRegistry) getFileStat(path string) (os.FileInfo, error) {
	return os.Stat(path)
}