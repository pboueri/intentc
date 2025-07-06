package logger

import (
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// Level represents the logging level
type Level int

const (
	DebugLevel Level = iota
	InfoLevel
	WarnLevel
	ErrorLevel
)

func (l Level) String() string {
	switch l {
	case DebugLevel:
		return "DEBUG"
	case InfoLevel:
		return "INFO"
	case WarnLevel:
		return "WARN"
	case ErrorLevel:
		return "ERROR"
	default:
		return "UNKNOWN"
	}
}

// Logger is the main logger interface
type Logger interface {
	Debug(format string, args ...interface{})
	Info(format string, args ...interface{})
	Warn(format string, args ...interface{})
	Error(format string, args ...interface{})
	SetLevel(level Level)
	Close() error
}

// Sink represents a logging destination
type Sink interface {
	Write(level Level, timestamp time.Time, message string) error
	Close() error
}

// ConsoleSink writes logs to stdout/stderr
type ConsoleSink struct {
	useStderr bool
	colorize  bool
}

func NewConsoleSink(useStderr, colorize bool) *ConsoleSink {
	return &ConsoleSink{
		useStderr: useStderr,
		colorize:  colorize,
	}
}

func (s *ConsoleSink) Write(level Level, timestamp time.Time, message string) error {
	output := os.Stdout
	if s.useStderr && (level == WarnLevel || level == ErrorLevel) {
		output = os.Stderr
	}

	var prefix string
	if s.colorize {
		// ANSI color codes
		switch level {
		case DebugLevel:
			prefix = "\033[36m" // Cyan
		case InfoLevel:
			prefix = "\033[32m" // Green
		case WarnLevel:
			prefix = "\033[33m" // Yellow
		case ErrorLevel:
			prefix = "\033[31m" // Red
		}
		defer fmt.Fprint(output, "\033[0m") // Reset
	}

	_, err := fmt.Fprintf(output, "%s[%s] %s: %s\n", 
		prefix,
		timestamp.Format("15:04:05"),
		level.String(),
		message)
	return err
}

func (s *ConsoleSink) Close() error {
	return nil
}

// FileSink writes logs to a file
type FileSink struct {
	file     *os.File
	mu       sync.Mutex
	filename string
}

func NewFileSink(filename string) (*FileSink, error) {
	// Create directory if it doesn't exist
	dir := filepath.Dir(filename)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return nil, fmt.Errorf("failed to create log directory: %w", err)
	}

	// Open file with append mode
	file, err := os.OpenFile(filename, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err != nil {
		return nil, fmt.Errorf("failed to open log file: %w", err)
	}

	return &FileSink{
		file:     file,
		filename: filename,
	}, nil
}

func (s *FileSink) Write(level Level, timestamp time.Time, message string) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	_, err := fmt.Fprintf(s.file, "[%s] %s: %s\n",
		timestamp.Format("2006-01-02 15:04:05"),
		level.String(),
		message)
	return err
}

func (s *FileSink) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.file.Close()
}

// MultiLogger implements Logger interface with multiple sinks
type MultiLogger struct {
	sinks []Sink
	level Level
	mu    sync.RWMutex
}

func NewMultiLogger(sinks ...Sink) *MultiLogger {
	return &MultiLogger{
		sinks: sinks,
		level: InfoLevel,
	}
}

func (l *MultiLogger) log(level Level, format string, args ...interface{}) {
	l.mu.RLock()
	currentLevel := l.level
	l.mu.RUnlock()

	if level < currentLevel {
		return
	}

	message := fmt.Sprintf(format, args...)
	timestamp := time.Now()

	for _, sink := range l.sinks {
		if err := sink.Write(level, timestamp, message); err != nil {
			// If we can't write to a sink, try stderr as fallback
			log.Printf("Failed to write to sink: %v", err)
		}
	}
}

func (l *MultiLogger) Debug(format string, args ...interface{}) {
	l.log(DebugLevel, format, args...)
}

func (l *MultiLogger) Info(format string, args ...interface{}) {
	l.log(InfoLevel, format, args...)
}

func (l *MultiLogger) Warn(format string, args ...interface{}) {
	l.log(WarnLevel, format, args...)
}

func (l *MultiLogger) Error(format string, args ...interface{}) {
	l.log(ErrorLevel, format, args...)
}

func (l *MultiLogger) SetLevel(level Level) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.level = level
}

func (l *MultiLogger) Close() error {
	var errs []error
	for _, sink := range l.sinks {
		if err := sink.Close(); err != nil {
			errs = append(errs, err)
		}
	}
	if len(errs) > 0 {
		return fmt.Errorf("failed to close %d sinks", len(errs))
	}
	return nil
}

// Global logger instance
var (
	globalLogger Logger
	once         sync.Once
)

// Initialize sets up the global logger
func Initialize(sinks ...Sink) {
	once.Do(func() {
		if len(sinks) == 0 {
			// Default to console sink
			sinks = []Sink{NewConsoleSink(false, true)}
		}
		globalLogger = NewMultiLogger(sinks...)
	})
}

// Get returns the global logger instance
func Get() Logger {
	if globalLogger == nil {
		Initialize()
	}
	return globalLogger
}

// Convenience functions that use the global logger
func Debug(format string, args ...interface{}) {
	Get().Debug(format, args...)
}

func Info(format string, args ...interface{}) {
	Get().Info(format, args...)
}

func Warn(format string, args ...interface{}) {
	Get().Warn(format, args...)
}

func Error(format string, args ...interface{}) {
	Get().Error(format, args...)
}

func SetLevel(level Level) {
	Get().SetLevel(level)
}

// ParseLevel converts a string to a Level
func ParseLevel(s string) (Level, error) {
	switch s {
	case "debug", "DEBUG":
		return DebugLevel, nil
	case "info", "INFO":
		return InfoLevel, nil
	case "warn", "WARN":
		return WarnLevel, nil
	case "error", "ERROR":
		return ErrorLevel, nil
	default:
		return InfoLevel, fmt.Errorf("unknown log level: %s", s)
	}
}