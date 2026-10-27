#!/usr/bin/env python3
import os
import re
import subprocess
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

class TestSecrets(unittest.TestCase):
    def test_no_git_tracked_file_contains_telegram_token(self):
        token_regex = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")
        
        # Get list of all git-tracked files
        cmd = ["git", "ls-files"]
        res = subprocess.run(cmd, cwd=str(BASE_DIR), capture_output=True, text=True, check=True)
        files = res.stdout.strip().splitlines()

        violating_files = []
        for rel_path in files:
            full_path = BASE_DIR / rel_path
            if not full_path.is_file():
                continue
            
            # Read as text
            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    if token_regex.search(content):
                        violating_files.append(rel_path)
            except Exception:
                pass

        self.assertEqual(
            violating_files, [],
            f"Found potential Telegram Bot Token in git-tracked files: {violating_files}"
        )

if __name__ == "__main__":
    unittest.main()
