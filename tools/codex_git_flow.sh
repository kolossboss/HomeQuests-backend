#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Nutzung:
  ./tools/codex_git_flow.sh start <name>
  ./tools/codex_git_flow.sh save "<commit-message>"
  ./tools/codex_git_flow.sh status

Befehle:
  start   Erstellt oder wechselt zu codex/<name> und pusht den Branch.
  save    Fuegt alle Aenderungen hinzu, committet und pusht (nur auf codex/*).
  status  Zeigt Branch/Remote/Arbeitsbaum.
EOF
}

require_repo() {
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
    echo "Fehler: Kein Git-Repo."
    exit 1
  }
}

current_branch() {
  git branch --show-current
}

cmd_start() {
  local name="${1:-}"
  if [[ -z "$name" ]]; then
    echo "Fehler: Bitte einen Branchnamen angeben, z. B. feature-login."
    exit 1
  fi

  local branch="codex/$name"
  if git show-ref --verify --quiet "refs/heads/$branch"; then
    git switch "$branch"
  else
    git switch -c "$branch"
  fi

  git push -u origin "$branch"
  echo "Aktiver Branch: $branch"
}

cmd_save() {
  local message="${1:-}"
  if [[ -z "$message" ]]; then
    echo "Fehler: Commit-Message fehlt."
    exit 1
  fi

  local branch
  branch="$(current_branch)"
  if [[ ! "$branch" =~ ^codex/ ]]; then
    echo "Fehler: save ist nur auf codex/* erlaubt. Aktuell: $branch"
    echo "Tipp: ./tools/codex_git_flow.sh start <name>"
    exit 1
  fi

  git add -A
  if git diff --cached --quiet; then
    echo "Keine Aenderungen zum Committen."
    exit 0
  fi

  git commit -m "$message"
  git push
  echo "Commit + Push fertig auf $branch"
}

cmd_status() {
  echo "Branch: $(current_branch)"
  echo
  git remote -v
  echo
  git status --short --branch
}

main() {
  require_repo
  cd "$(git rev-parse --show-toplevel)"

  local cmd="${1:-}"
  shift || true

  case "$cmd" in
    start) cmd_start "${1:-}" ;;
    save) cmd_save "${1:-}" ;;
    status) cmd_status ;;
    ""|help|-h|--help) usage ;;
    *)
      echo "Unbekannter Befehl: $cmd"
      usage
      exit 1
      ;;
  esac
}

main "$@"
