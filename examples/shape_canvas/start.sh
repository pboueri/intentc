#!/bin/bash

FILE_PATH="$(cd "$(dirname "$0")" && pwd)/src/index.html"

echo "Opening Shape Canvas..."
echo "File: file://$FILE_PATH"

if command -v open &> /dev/null; then
  open "$FILE_PATH"
elif command -v xdg-open &> /dev/null; then
  xdg-open "$FILE_PATH"
else
  echo "Could not detect a way to open the browser. Please open the file manually:"
  echo "  file://$FILE_PATH"
fi
