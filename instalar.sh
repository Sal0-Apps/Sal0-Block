#!/usr/bin/env bash
set -Eeuo pipefail
task_app_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$task_app_dir"
if [[ "${EUID}" -ne 0 ]]; then
  echo "Execute: sudo bash instalar.sh"; exit 1
fi
command -v docker >/dev/null || { echo "Docker não encontrado."; exit 1; }
if docker compose version >/dev/null 2>&1; then
  task_compose=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  task_compose=(docker-compose)
else
  echo "Docker Compose não encontrado."; exit 1
fi
task_data_dir="${ISB_DATA_PATH:-}"
task_web_port="${SAL0_WEB_PORT:-}"
# Read only our own literal values; never execute a dotenv file as shell code.
if [[ -f .env ]]; then
  [[ -n "$task_data_dir" ]] || task_data_dir="$(sed -n "s/^ISB_DATA_PATH='\(.*\)'$/\1/p" .env | tail -n1)"
  [[ -n "$task_web_port" ]] || task_web_port="$(sed -n 's/^SAL0_WEB_PORT=\([0-9]*\)$/\1/p' .env | tail -n1)"
fi
if [[ -z "$task_data_dir" ]]; then
  for task_candidate in sal0-block sal0-isponsorblocktv isponsorblocktv; do
    if docker inspect "$task_candidate" >/dev/null 2>&1; then
      task_candidate_path="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Source}}{{end}}{{end}}' "$task_candidate")"
      if [[ -n "$task_candidate_path" && -f "$task_candidate_path/config.json" ]]; then
        task_data_dir="$task_candidate_path"; break
      fi
    fi
  done
fi
task_data_dir="${task_data_dir:-/DATA/AppData/sal0-isponsorblocktv/data}"
task_web_port="${task_web_port:-7889}"
[[ "$task_data_dir" == /* && "$task_data_dir" != *"'"* && "$task_data_dir" != *$'\n'* ]] || { echo "Pasta de dados inválida; use um caminho absoluto sem apóstrofos."; exit 1; }
[[ "$task_web_port" =~ ^[0-9]+$ ]] && ((task_web_port > 0 && task_web_port < 65536)) || { echo "Porta inválida."; exit 1; }
mkdir -p "$task_data_dir"
export ISB_DATA_PATH="$task_data_dir" SAL0_WEB_PORT="$task_web_port"
task_compose_args=(-p sal0-block -f "$task_app_dir/docker-compose-completo.yml")
"${task_compose[@]}" "${task_compose_args[@]}" config -q
echo "Verificando as duas imagens antes da migração…"
"${task_compose[@]}" "${task_compose_args[@]}" pull

task_old_names=()
task_old_archives=()
task_old_running=()
task_renamed=()
task_stamp="$(date +%Y%m%d%H%M%S)"
task_rollback() {
  local task_result=$?
  trap - ERR INT TERM
  if ((${#task_old_names[@]})); then
    echo "A migração falhou. Restaurando os containers anteriores."
    # Remove only replacements created by this installation, without touching bind data.
    for task_new in sal0-block isponsorblocktv; do
      if [[ "$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$task_new" 2>/dev/null || true)" == sal0-block ]]; then
        docker rm -f "$task_new" >/dev/null 2>&1 || true
      fi
    done
    for ((task_i=${#task_old_names[@]}-1; task_i>=0; task_i--)); do
      if [[ "${task_renamed[$task_i]}" == true ]]; then
        docker rename "${task_old_archives[$task_i]}" "${task_old_names[$task_i]}" || true
      fi
      if [[ "${task_old_running[$task_i]}" == true ]]; then
        docker start "${task_old_names[$task_i]}" >/dev/null || true
      fi
    done
  fi
  ((task_result)) || task_result=1
  exit "$task_result"
}
trap task_rollback ERR INT TERM
for task_old in isponsorblocktv sal0-isponsorblocktv sal0-block; do
  if ! docker inspect "$task_old" >/dev/null 2>&1; then continue; fi
  task_project="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$task_old")"
  if [[ "$task_project" == sal0-block && "$task_old" != sal0-isponsorblocktv ]]; then continue; fi
  task_index=${#task_old_names[@]}
  task_old_names+=("$task_old")
  task_old_archives+=("${task_old}-before-sal0block-${task_stamp}")
  task_old_running+=("$(docker inspect -f '{{.State.Running}}' "$task_old")")
  task_renamed+=(false)
  docker stop "$task_old" >/dev/null
  docker rename "$task_old" "${task_old_archives[$task_index]}"
  task_renamed[$task_index]=true
done
"${task_compose[@]}" "${task_compose_args[@]}" up -d
task_ready=false
for ((task_attempt=0; task_attempt<30; task_attempt++)); do
  if docker exec sal0-block python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/healthz', timeout=2).read()" >/dev/null 2>&1; then
    task_ready=true; break
  fi
  sleep 2
done
if [[ "$task_ready" != true ]]; then
  docker logs --tail 30 sal0-block || true
  false
fi
printf "ISB_DATA_PATH='%s'\nSAL0_WEB_PORT=%s\n" "$task_data_dir" "$task_web_port" > .env
chmod 600 .env
trap - ERR INT TERM
echo "Sal0 Block instalado: http://IP-DO-SERVIDOR:${task_web_port}"
echo "Configuração compartilhada: ${task_data_dir}"
echo "O iSponsorBlockTV oficial e o painel são atualizados separadamente."
if ((${#task_old_archives[@]})); then
  echo "Containers anteriores preservados e parados:"
  printf '  %s\n' "${task_old_archives[@]}"
fi
