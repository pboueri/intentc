package util

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// EnsureDir creates a directory if it doesn't exist
func EnsureDir(dir string) error {
	if err := os.MkdirAll(dir, 0755); err != nil {
		return fmt.Errorf("failed to create directory %s: %w", dir, err)
	}
	return nil
}

// FileExists checks if a file exists
func FileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

// IsDirectory checks if a path is a directory
func IsDirectory(path string) bool {
	info, err := os.Stat(path)
	if err != nil {
		return false
	}
	return info.IsDir()
}

// MakeAbsolute converts a relative path to absolute based on a root directory
func MakeAbsolute(path, root string) string {
	if filepath.IsAbs(path) {
		return path
	}
	return filepath.Join(root, path)
}

// NormalizePath cleans and normalizes a file path
func NormalizePath(path string) string {
	path = filepath.Clean(path)
	path = strings.TrimSpace(path)
	return path
}

// RelativePath returns the relative path from base to target
func RelativePath(base, target string) (string, error) {
	return filepath.Rel(base, target)
}

// FindFilesWithExtension recursively finds all files with a specific extension
func FindFilesWithExtension(root string, ext string) ([]string, error) {
	var files []string
	
	err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if !info.IsDir() && strings.HasSuffix(path, ext) {
			files = append(files, path)
		}
		return nil
	})
	
	return files, err
}

// CopyFile copies a file from src to dst
func CopyFile(src, dst string) error {
	data, err := os.ReadFile(src)
	if err != nil {
		return fmt.Errorf("failed to read source file: %w", err)
	}
	
	if err := os.WriteFile(dst, data, 0644); err != nil {
		return fmt.Errorf("failed to write destination file: %w", err)
	}
	
	return nil
}

// GetProjectRoot finds the project root by looking for .intentc directory
func GetProjectRoot(startPath string) (string, error) {
	current := startPath
	
	for {
		configPath := filepath.Join(current, ".intentc")
		if FileExists(configPath) {
			return current, nil
		}
		
		parent := filepath.Dir(current)
		if parent == current {
			// Reached root of filesystem
			break
		}
		current = parent
	}
	
	return "", fmt.Errorf("not in an intentc project (no .intentc found)")
}

// CleanFilePath extracts a clean file path from a string that may contain extra characters
func CleanFilePath(pathStr string) string {
	// Remove common delimiters and quotes
	pathStr = strings.Trim(pathStr, "\"'`")
	pathStr = strings.TrimSuffix(pathStr, ":")
	pathStr = strings.TrimSuffix(pathStr, ",")
	pathStr = strings.TrimSpace(pathStr)
	
	return pathStr
}

// SplitPath splits a file path into directory and filename components
func SplitPath(path string) (dir, file string) {
	dir = filepath.Dir(path)
	file = filepath.Base(path)
	return
}

// HasExtension checks if a filename has any of the given extensions
func HasExtension(filename string, extensions ...string) bool {
	for _, ext := range extensions {
		if strings.HasSuffix(filename, ext) {
			return true
		}
	}
	return false
}

// RemoveExtension removes the file extension from a filename
func RemoveExtension(filename string) string {
	ext := filepath.Ext(filename)
	if ext != "" {
		return filename[:len(filename)-len(ext)]
	}
	return filename
}

// EnsureFileDir ensures the directory for a file path exists
func EnsureFileDir(filePath string) error {
	dir := filepath.Dir(filePath)
	return EnsureDir(dir)
}