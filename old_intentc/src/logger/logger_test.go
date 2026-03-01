package logger

import (
	"bytes"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// MemorySink for testing
type MemorySink struct {
	messages []string
}

func NewMemorySink() *MemorySink {
	return &MemorySink{
		messages: make([]string, 0),
	}
}

func (s *MemorySink) Write(level Level, timestamp time.Time, message string) error {
	s.messages = append(s.messages, message)
	return nil
}

func (s *MemorySink) Close() error {
	return nil
}

func TestLogLevels(t *testing.T) {
	tests := []struct {
		level    Level
		expected string
	}{
		{DebugLevel, "DEBUG"},
		{InfoLevel, "INFO"},
		{WarnLevel, "WARN"},
		{ErrorLevel, "ERROR"},
	}

	for _, tt := range tests {
		t.Run(tt.expected, func(t *testing.T) {
			assert.Equal(t, tt.expected, tt.level.String())
		})
	}
}

func TestMultiLogger(t *testing.T) {
	sink := NewMemorySink()
	logger := NewMultiLogger(sink)

	// Test different log levels
	logger.SetLevel(InfoLevel)
	
	logger.Debug("debug message")
	logger.Info("info message")
	logger.Warn("warn message")
	logger.Error("error message")

	// Debug should be filtered out
	assert.Equal(t, 3, len(sink.messages))
	assert.Contains(t, sink.messages[0], "info message")
	assert.Contains(t, sink.messages[1], "warn message")
	assert.Contains(t, sink.messages[2], "error message")
}

func TestFileSink(t *testing.T) {
	tempDir := t.TempDir()
	logFile := filepath.Join(tempDir, "test.log")

	sink, err := NewFileSink(logFile)
	require.NoError(t, err)
	defer sink.Close()

	logger := NewMultiLogger(sink)
	logger.Info("test message")

	// Read the log file
	content, err := os.ReadFile(logFile)
	require.NoError(t, err)
	assert.Contains(t, string(content), "INFO: test message")
}

func TestParseLevel(t *testing.T) {
	tests := []struct {
		input    string
		expected Level
		hasError bool
	}{
		{"debug", DebugLevel, false},
		{"DEBUG", DebugLevel, false},
		{"info", InfoLevel, false},
		{"INFO", InfoLevel, false},
		{"warn", WarnLevel, false},
		{"WARN", WarnLevel, false},
		{"error", ErrorLevel, false},
		{"ERROR", ErrorLevel, false},
		{"invalid", InfoLevel, true},
	}

	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			level, err := ParseLevel(tt.input)
			if tt.hasError {
				assert.Error(t, err)
			} else {
				assert.NoError(t, err)
				assert.Equal(t, tt.expected, level)
			}
		})
	}
}

func TestConsoleSink(t *testing.T) {
	// Capture stdout
	old := os.Stdout
	r, w, _ := os.Pipe()
	os.Stdout = w

	sink := NewConsoleSink(false, false)
	sink.Write(InfoLevel, time.Now(), "test message")

	w.Close()
	os.Stdout = old

	var buf bytes.Buffer
	buf.ReadFrom(r)
	output := buf.String()

	assert.Contains(t, output, "INFO: test message")
}

func TestGlobalLogger(t *testing.T) {
	// Reset global logger for testing
	globalLogger = nil
	once = sync.Once{}

	sink := NewMemorySink()
	Initialize(sink)

	Info("global info message")
	Error("global error message")

	assert.Equal(t, 2, len(sink.messages))
	assert.Contains(t, sink.messages[0], "global info message")
	assert.Contains(t, sink.messages[1], "global error message")
}

func TestMultipleSinks(t *testing.T) {
	sink1 := NewMemorySink()
	sink2 := NewMemorySink()
	
	logger := NewMultiLogger(sink1, sink2)
	logger.Info("broadcast message")

	assert.Equal(t, 1, len(sink1.messages))
	assert.Equal(t, 1, len(sink2.messages))
	assert.Contains(t, sink1.messages[0], "broadcast message")
	assert.Contains(t, sink2.messages[0], "broadcast message")
}