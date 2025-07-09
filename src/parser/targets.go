package parser

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/pboueri/intentc/src"
)

type TargetInfo struct {
	Name            string
	Intent          *src.Intent
	ValidationFiles []*src.ValidationFile
	IntentPath      string
	LastModified    time.Time
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
	intent       *src.Intent
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
		parser:      New(),
	}
}

func (r *TargetRegistry) LoadTargets() error {
	// Clear existing targets
	r.targets = make(map[string]*TargetInfo)
	
	// Discover all feature directories in intent/
	intentsDir := filepath.Join(r.projectRoot, "intent")
	if _, err := os.Stat(intentsDir); os.IsNotExist(err) {
		return nil // No intents directory yet
	}
	
	entries, err := os.ReadDir(intentsDir)
	if err != nil {
		return fmt.Errorf("failed to read intents directory: %w", err)
	}
	
	for _, entry := range entries {
		if entry.IsDir() {
			dirPath := filepath.Join(intentsDir, entry.Name())
			if r.parser.hasIntentFile(dirPath) {
				if err := r.loadTargetFromDirectory(dirPath); err != nil {
					return fmt.Errorf("failed to load target from %s: %w", dirPath, err)
				}
			}
		}
	}
	
	// Set up default aliases
	r.setupDefaultAliases()
	
	return nil
}

func (r *TargetRegistry) loadTargetFromDirectory(dirPath string) error {
	// Find the .ic file
	intentPath, err := r.parser.FindIntentFile(dirPath)
	if err != nil {
		return err
	}
	
	// Parse the intent file
	intent, err := r.parser.ParseIntentFile(intentPath)
	if err != nil {
		return fmt.Errorf("failed to parse intent file: %w", err)
	}
	
	// Get file modification time
	stat, err := os.Stat(intentPath)
	if err != nil {
		return fmt.Errorf("failed to stat intent file: %w", err)
	}
	
	// Parse validation files
	validationFiles, err := r.parser.ParseValidationFiles(dirPath)
	if err != nil {
		return fmt.Errorf("failed to parse validation files: %w", err)
	}
	
	target := &TargetInfo{
		Name:            intent.Name,
		Intent:          intent,
		ValidationFiles: validationFiles,
		IntentPath:      intentPath,
		LastModified:    stat.ModTime(),
	}
	
	r.targets[intent.Name] = target
	
	return nil
}

func (r *TargetRegistry) GetTarget(name string) (*TargetInfo, bool) {
	// Check if it's an alias
	if targets, exists := r.aliases[name]; exists && len(targets) > 0 {
		// Return the first target in the alias
		return r.GetTarget(targets[0])
	}
	
	target, exists := r.targets[name]
	return target, exists
}

func (r *TargetRegistry) GetAllTargets() []*TargetInfo {
	var targets []*TargetInfo
	for _, target := range r.targets {
		targets = append(targets, target)
	}
	return targets
}

func (r *TargetRegistry) GetCachedIntent(path string) (*src.Intent, bool) {
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

func (r *TargetRegistry) CacheIntent(path string, intent *src.Intent) {
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

func (r *TargetRegistry) setupDefaultAliases() {
	// All targets alias
	var allTargets []string
	for name := range r.targets {
		allTargets = append(allTargets, name)
	}
	if len(allTargets) > 0 {
		r.aliases["all"] = allTargets
	}
	
	// Project targets alias - targets that start with "project-"
	var projectTargets []string
	for name := range r.targets {
		if strings.HasPrefix(name, "project-") {
			projectTargets = append(projectTargets, name)
		}
	}
	if len(projectTargets) > 0 {
		r.aliases["project"] = projectTargets
	}
	
	// Feature targets alias - all non-project targets
	var featureTargets []string
	for name := range r.targets {
		if !strings.HasPrefix(name, "project-") {
			featureTargets = append(featureTargets, name)
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
	
	// Invalidate cache
	r.InvalidateCache(target.IntentPath)
	
	// Re-parse the intent file
	intent, err := r.parser.ParseIntentFile(target.IntentPath)
	if err != nil {
		return fmt.Errorf("failed to parse intent file: %w", err)
	}
	
	// Update the target
	target.Intent = intent
	
	// Update modification time
	stat, err := os.Stat(target.IntentPath)
	if err == nil {
		target.LastModified = stat.ModTime()
	}
	
	// Re-parse validation files
	dirPath := filepath.Dir(target.IntentPath)
	validationFiles, err := r.parser.ParseValidationFiles(dirPath)
	if err == nil {
		target.ValidationFiles = validationFiles
	}
	
	// Cache the parsed intent
	r.CacheIntent(target.IntentPath, intent)
	
	return nil
}