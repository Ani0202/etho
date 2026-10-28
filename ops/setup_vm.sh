#!/usr/bin/env bash
# One-time setup for the always-on dev box (Oracle free tier, Ubuntu 22.04/24.04, x86_64 or arm64).
# Usage: bash setup_vm.sh
set -euo pipefail

if ! command -v apt-get >/dev/null; then
  echo "This script expects Ubuntu/Debian. On Oracle Linux, install the same packages with dnf." >&2
  exit 1
fi

echo "==> System packages"
sudo apt-get update -y
sudo apt-get install -y git tmux ffmpeg jq curl build-essential python3-venv python3-dev unzip gh bubblewrap

echo "==> uv (Python env manager)"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh

echo "==> Claude Code (native installer)"
command -v claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"
grep -q 'local/bin' ~/.bashrc || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc

echo "==> Codex CLI (OpenAI), signed in with your ChatGPT plan"
if ! command -v codex >/dev/null; then
  if command -v npm >/dev/null; then sudo npm install -g @openai/codex
  else echo "  npm not found: install Codex via https://learn.chatgpt.com/docs/codex/cli (standalone installer)"; fi
fi

echo "==> Codex config: always bill the ChatGPT plan, never an API key"
mkdir -p ~/.codex
grep -q '^forced_login_method' ~/.codex/config.toml 2>/dev/null || echo 'forced_login_method = "chatgpt"' >> ~/.codex/config.toml

echo "==> Secrets file (outside the repo, owner-only)"
mkdir -p ~/.config/etho
if [ ! -f ~/.config/etho/secrets.env ]; then
  cat > ~/.config/etho/secrets.env <<'SECRETS'
# Fill these in. Never commit this file.
BASETEN_API_KEY=
META_MODEL_API_KEY=
# Optional, once granted:
MODAL_TOKEN_ID=
MODAL_TOKEN_SECRET=
FIREWORKS_API_KEY=
# Optional (paid by you), only if you want serverless Glimmer:
TOGETHER_API_KEY=
SECRETS
fi
chmod 600 ~/.config/etho/secrets.env

echo "==> Workspace"
mkdir -p ~/work ~/work/etho-wt
echo
echo "Done. Next:"
echo "  1. nano ~/.config/etho/secrets.env   (paste keys)"
echo "  2. gh auth login                       (create a PRIVATE repo 'etho')"
echo "  3. git clone <repo> ~/work/etho && copy this kit's files into it"
echo "  4. codex login   (choose ChatGPT sign-in; use the device-auth option on a headless VM)"
echo "  5. tmux new -s etho ; cd ~/work/etho ; claude   (then /login with your Claude account)"
