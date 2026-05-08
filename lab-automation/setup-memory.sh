#!/usr/bin/env bash
# setup-memory.sh — Bootstrap LLM long-term memory on a new macOS machine
#
# Run once per machine under the ijosh-ch GitHub account:
#   bash lab-automation/setup-memory.sh
#
# What this does:
#   1. Installs autossh (Homebrew)
#   2. Creates the launchd tunnel plist (localhost:3307 → wifi@140.118.122.119:3306)
#   3. Writes ~/.claude/settings.json  (Claude Code MCP)
#   4. Writes ~/CLAUDE.md              (global Claude memory instructions)
#   5. Creates ~/.copilot/instructions.md (global Copilot instructions)
#   6. Prints the VS Code settings.json snippet to add manually

set -euo pipefail

VM_SSH_TARGET="wifi@140.118.122.119"
LOCAL_PORT="3307"
MYSQL_USER="llmuser"
MYSQL_DB="llm_memory"
PLIST="$HOME/Library/LaunchAgents/com.bmwlab.mysql-tunnel.plist"

echo "=== BMW Lab LLM Memory Setup ==="
echo
read -rsp "MySQL password for $MYSQL_USER@localhost:$LOCAL_PORT: " MYSQL_PASS
echo

# ── 1. autossh ────────────────────────────────────────────────────────────────
if ! command -v autossh &>/dev/null; then
  echo "Installing autossh..."
  brew install autossh
fi
AUTOSSH_BIN=$(which autossh)
echo "✓ autossh: $AUTOSSH_BIN"

# ── 2. launchd plist ──────────────────────────────────────────────────────────
mkdir -p "$(dirname "$PLIST")"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.bmwlab.mysql-tunnel</string>
    <key>ProgramArguments</key>
    <array>
        <string>$AUTOSSH_BIN</string>
        <string>-M</string><string>0</string>
        <string>-N</string>
        <string>-o</string><string>ServerAliveInterval=30</string>
        <string>-o</string><string>ServerAliveCountMax=3</string>
        <string>-o</string><string>ExitOnForwardFailure=yes</string>
        <string>-L</string><string>${LOCAL_PORT}:127.0.0.1:3306</string>
        <string>$VM_SSH_TARGET</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardErrorPath</key>
    <string>/tmp/mysql-tunnel.err</string>
</dict>
</plist>
PLIST_EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
sleep 2
nc -z 127.0.0.1 "$LOCAL_PORT" && echo "✓ SSH tunnel up on localhost:$LOCAL_PORT" \
  || echo "⚠ Tunnel not responding — check SSH key auth to $VM_SSH_TARGET"

# ── 3. ~/.claude/settings.json ────────────────────────────────────────────────
mkdir -p "$HOME/.claude"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"

if [ -f "$CLAUDE_SETTINGS" ]; then
  # Merge: add mcpServers key if missing, preserving other keys
  python3 - <<PYEOF
import json, sys
path = "$CLAUDE_SETTINGS"
with open(path) as f:
    cfg = json.load(f)
cfg.setdefault("mcpServers", {})["mysql-memory"] = {
    "command": "npx",
    "args": ["-y", "@benborla29/mcp-server-mysql"],
    "env": {
        "MYSQL_HOST": "127.0.0.1",
        "MYSQL_PORT": "$LOCAL_PORT",
        "MYSQL_USER": "$MYSQL_USER",
        "MYSQL_PASS": "$MYSQL_PASS",
        "MYSQL_DB": "$MYSQL_DB"
    }
}
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
print("✓ ~/.claude/settings.json updated")
PYEOF
else
  cat > "$CLAUDE_SETTINGS" <<JSON_EOF
{
  "mcpServers": {
    "mysql-memory": {
      "command": "npx",
      "args": ["-y", "@benborla29/mcp-server-mysql"],
      "env": {
        "MYSQL_HOST": "127.0.0.1",
        "MYSQL_PORT": "$LOCAL_PORT",
        "MYSQL_USER": "$MYSQL_USER",
        "MYSQL_PASS": "$MYSQL_PASS",
        "MYSQL_DB": "$MYSQL_DB"
      }
    }
  }
}
JSON_EOF
  echo "✓ ~/.claude/settings.json created"
fi

# ── 4. ~/CLAUDE.md (global Claude instructions) ───────────────────────────────
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cp "$REPO_DIR/lab-automation/global-claude.md" "$HOME/CLAUDE.md"
echo "✓ ~/CLAUDE.md written"

# ── 5. ~/.copilot/instructions.md (global Copilot instructions) ───────────────
mkdir -p "$HOME/.copilot"
cp "$REPO_DIR/lab-automation/global-copilot.md" "$HOME/.copilot/instructions.md"
echo "✓ ~/.copilot/instructions.md written"

# ── 6. VS Code settings snippet ───────────────────────────────────────────────
echo
echo "══════════════════════════════════════════════════"
echo "Add the following to VS Code user settings.json"
echo "(Cmd+Shift+P → Preferences: Open User Settings JSON)"
echo "══════════════════════════════════════════════════"
cat <<VSCODE_EOF

  "mcp": {
    "servers": {
      "mysql-memory": {
        "command": "npx",
        "args": ["-y", "@benborla29/mcp-server-mysql"],
        "env": {
          "MYSQL_HOST": "127.0.0.1",
          "MYSQL_PORT": "$LOCAL_PORT",
          "MYSQL_USER": "$MYSQL_USER",
          "MYSQL_PASS": "$MYSQL_PASS",
          "MYSQL_DB": "$MYSQL_DB"
        }
      }
    }
  },
  "chat.instructionsFilesLocations": {
    "~/.copilot/instructions.md": true
  },

VSCODE_EOF

echo "══════════════════════════════════════════════════"
echo "Setup complete. Restart VS Code to activate MCP."
