"""
Módulo de Trava de Alta Disponibilidade Líder / Standby entre Múltiplos Notebooks.
Utiliza um arquivo distribuído de heartbeat no Google Drive ('live_recorder_cluster.lock')
para garantir que apenas 1 notebook esteja capturando ou gravando ativamente (Líder),
enquanto outros notebooks permanecem em Standby Protegido sem sobrepor dados.
"""

from datetime import datetime, timezone
import io
import json
import logging
import os
from pathlib import Path
import socket
import threading
import time
from typing import Any, Dict, Optional
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import config, google_drive

log = logging.getLogger("indomavel.cluster")

LOCK_FILENAME = "live_recorder_cluster.lock"
HEARTBEAT_INTERVAL_SECONDS = 30
LOCK_TIMEOUT_SECONDS = 90
PASTA_CLUSTER_PADRAO = "1Rzc1NQ0RDzeId6L_7O8QiP1P13bTooFl"  # Pasta 'Live 24 hrs'


def get_default_machine_id() -> str:
    """Gera identificador único e persistente para a máquina atual."""
    hostname = socket.gethostname()
    node_hex = hex(uuid.getnode())[2:8]
    return f"{hostname}_{node_hex}"


class ClusterLockManager:
    """
    Gerenciador da eleição distribuída Líder / Standby via Google Drive.
    Garante que se você abrir o Indomável em 2 notebooks:
    - O 1º notebook vira LÍDER e grava/sobe arquivos.
    - O 2º notebook vira STANDBY e NÃO grava nada, não compete por fatias nem sobrepõe dados.
    - Se o Líder fechar ou cair por >90s, ocorre failover automático.
    """

    def __init__(
        self,
        machine_id: Optional[str] = None,
        root_folder_id: Optional[str] = None,
    ):
        self.machine_id = machine_id or get_default_machine_id()
        self.hostname = socket.gethostname()
        self.root_folder_id = root_folder_id or PASTA_CLUSTER_PADRAO

        self._is_leader = False
        self._lock_file_id: Optional[str] = None
        self._last_leader_info: Dict[str, Any] = {}
        self._lock = threading.Lock()

        self._stop_event = threading.Event()
        self._heartbeat_thread: Optional[threading.Thread] = None

        self._on_leadership_claimed_cb: Optional[Any] = None
        self._on_leadership_lost_cb: Optional[Any] = None

    def set_callbacks(
        self,
        on_leadership_claimed: Optional[Any] = None,
        on_leadership_lost: Optional[Any] = None
    ) -> None:
        """Registra callbacks para eventos de mudança de liderança / failover."""
        self._on_leadership_claimed_cb = on_leadership_claimed
        self._on_leadership_lost_cb = on_leadership_lost

    @property
    def is_leader(self) -> bool:
        with self._lock:
            return self._is_leader

    @property
    def role_name(self) -> str:
        with self._lock:
            return "LEADER" if self._is_leader else "STANDBY"

    @property
    def current_role(self) -> str:
        with self._lock:
            return "leader" if self._is_leader else "standby"

    @property
    def current_leader_id(self) -> str:
        with self._lock:
            if self._is_leader:
                return self.machine_id
            return self._last_leader_info.get("leader_machine_id", self.machine_id)

    def get_status_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "machine_id": self.machine_id,
                "hostname": self.hostname,
                "role": "LEADER" if self._is_leader else "STANDBY",
                "is_leader": self._is_leader,
                "leader_machine_id": self._last_leader_info.get("leader_machine_id", self.machine_id if self._is_leader else "nenhum"),
                "leader_hostname": self._last_leader_info.get("leader_hostname", self.hostname if self._is_leader else ""),
                "heartbeat_age_s": round(time.time() - self._last_leader_info.get("heartbeat_epoch", time.time()), 1) if self._last_leader_info else 0,
                "leader_info": self._last_leader_info
            }

    def start(self, live_title: str = "Live Stream") -> None:
        """Inicia a thread de monitoramento e heartbeat do cluster distribuído."""
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return
        self._stop_event.clear()
        try:
            self.evaluate_leadership(live_title)
        except Exception as e:
            log.warning("[Cluster] Falha na avaliação inicial de liderança: %s", e)

        self._heartbeat_thread = threading.Thread(
            target=self._run_cluster_loop,
            args=(live_title,),
            name="ClusterLockThread",
            daemon=True
        )
        self._heartbeat_thread.start()
        log.info("[Cluster] Gerenciador Líder/Standby ativo em '%s' (Papel: %s).", self.machine_id, self.role_name)

    def stop(self) -> None:
        """Encerra monitoramento e libera liderança se formos o líder."""
        self._stop_event.set()
        if self.is_leader:
            self.release_leadership()
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=3.0)

    def _run_cluster_loop(self, live_title: str) -> None:
        while not self._stop_event.is_set():
            try:
                self.evaluate_leadership(live_title)
            except Exception as e:
                log.warning("[Cluster] Erro ao verificar lock no Google Drive: %s", e)

            self._stop_event.wait(timeout=HEARTBEAT_INTERVAL_SECONDS)

    def evaluate_leadership(self, live_title: str = "Live Stream") -> bool:
        """
        Consulta o lock no Google Drive.
        Assume liderança se não houver líder ou se o líder anterior estiver inativo há >90s.
        """
        token, _ = google_drive.obter_token_acesso()
        if not token:
            # Sem nuvem ativa, opera localmente como líder autônomo seguro
            with self._lock:
                self._is_leader = True
            return True

        now_epoch = time.time()
        now_iso = datetime.now(timezone.utc).isoformat()

        try:
            # 1. Busca arquivo de lock no Google Drive
            lock_file_id, lock_data = self._obter_lock_drive(token)

            # 2. Se não existe lock ou está corrompido, reivindica liderança
            if not lock_data:
                log.info("[Cluster] Nenhum lock ativo no Drive. Reivindicando LIDERANÇA para %s...", self.machine_id)
                self._claim_leadership(lock_file_id, live_title, now_epoch, now_iso, previous_leader_id=None, token=token)
                return True

            leader_id = lock_data.get("leader_machine_id")
            last_heartbeat = lock_data.get("heartbeat_epoch", 0)
            elapsed_since_heartbeat = now_epoch - last_heartbeat

            with self._lock:
                self._last_leader_info = lock_data

            if leader_id == self.machine_id:
                # Nós somos o líder: atualiza o batimento
                self._update_heartbeat(lock_file_id, live_title, now_epoch, now_iso, token=token)
                with self._lock:
                    self._is_leader = True
                return True

            # Outra máquina é o líder registrado
            if elapsed_since_heartbeat > LOCK_TIMEOUT_SECONDS:
                log.warning(
                    "[Cluster] FAILOVER! Líder '%s' inativo há %.0fs (> %ds). Assumindo liderança nesta máquina!",
                    leader_id, elapsed_since_heartbeat, LOCK_TIMEOUT_SECONDS
                )
                self._claim_leadership(lock_file_id, live_title, now_epoch, now_iso, previous_leader_id=leader_id, token=token)
                return True
            else:
                # O outro notebook está ativo gravando: nós ficamos em STANDBY!
                lost_leadership = False
                with self._lock:
                    if self._is_leader:
                        log.info("[Cluster] Cedendo liderança para máquina ativa '%s'. Entrando em STANDBY.", leader_id)
                        lost_leadership = True
                    self._is_leader = False

                if lost_leadership and self._on_leadership_lost_cb:
                    try:
                        self._on_leadership_lost_cb(leader_id)
                    except Exception as cb_err:
                        log.debug("[Cluster] Erro no callback on_leadership_lost: %s", cb_err)

                log.debug("[Cluster] Standby ativo. Líder '%s' com heartbeat há %.0fs.", leader_id, elapsed_since_heartbeat)
                return False

        except Exception as err:
            log.warning("[Cluster] Erro na consulta do lock: %s", err)
            if self._last_leader_info.get("leader_machine_id") in (None, self.machine_id):
                with self._lock:
                    self._is_leader = True
            return self.is_leader

    def _obter_lock_drive(self, token: str):
        query = f"'{self.root_folder_id}' in parents and name = '{LOCK_FILENAME}' and trashed = false"
        url = f"https://www.googleapis.com/drive/v3/files?q={urllib.parse.quote(query)}&fields=files(id,name,modifiedTime)&pageSize=1"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        files = data.get("files", [])
        if not files:
            return None, None
        file_id = files[0]["id"]
        self._lock_file_id = file_id

        # Baixa conteúdo do lock
        url_content = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
        req_c = urllib.request.Request(url_content, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req_c, timeout=15) as resp_c:
                content = json.loads(resp_c.read().decode("utf-8"))
            return file_id, content
        except Exception:
            return file_id, None

    def _claim_leadership(self, file_id: Optional[str], live_title: str, now_epoch: float, now_iso: str, previous_leader_id: Optional[str], token: str):
        payload = {
            "leader_machine_id": self.machine_id,
            "leader_hostname": self.hostname,
            "heartbeat_timestamp": now_iso,
            "heartbeat_epoch": now_epoch,
            "live_title": live_title,
            "claimed_at": now_iso
        }
        body_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

        if file_id:
            url = f"https://www.googleapis.com/upload/drive/v3/files/{file_id}?uploadType=media"
            req = urllib.request.Request(url, data=body_bytes, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="PATCH")
            with urllib.request.urlopen(req, timeout=20):
                pass
        else:
            meta = {"name": LOCK_FILENAME, "parents": [self.root_folder_id]}
            boundary = "-------314159265358979323846"
            corpo = bytearray()
            corpo.extend(f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n".encode("utf-8"))
            corpo.extend(f"--{boundary}\r\nContent-Type: application/json\r\n\r\n".encode("utf-8"))
            corpo.extend(body_bytes)
            corpo.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))

            url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"
            req = urllib.request.Request(url, data=corpo, headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/related; boundary={boundary}"
            }, method="POST")
            with urllib.request.urlopen(req, timeout=25) as resp:
                created = json.loads(resp.read().decode("utf-8"))
                self._lock_file_id = created.get("id")

        was_leader = False
        with self._lock:
            was_leader = self._is_leader
            self._is_leader = True
            self._last_leader_info = payload
        log.info("[Cluster] 👑 Máquina '%s' assumiu LIDERANÇA da gravação.", self.machine_id)

        if (not was_leader or previous_leader_id) and self._on_leadership_claimed_cb:
            try:
                self._on_leadership_claimed_cb(self.machine_id, previous_leader_id, live_title)
            except Exception as cb_err:
                log.debug("[Cluster] Erro callback on_leadership_claimed: %s", cb_err)

    def _update_heartbeat(self, file_id: str, live_title: str, now_epoch: float, now_iso: str, token: str):
        payload = {
            "leader_machine_id": self.machine_id,
            "leader_hostname": self.hostname,
            "heartbeat_timestamp": now_iso,
            "heartbeat_epoch": now_epoch,
            "live_title": live_title
        }
        body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = f"https://www.googleapis.com/upload/drive/v3/files/{file_id}?uploadType=media"
        req = urllib.request.Request(url, data=body_bytes, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="PATCH")
        with urllib.request.urlopen(req, timeout=15):
            pass

    def release_leadership(self) -> None:
        """Libera liderança no Google Drive ao fechar ou desativar gravação."""
        token, _ = google_drive.obter_token_acesso()
        if not token or not self._lock_file_id:
            with self._lock:
                self._is_leader = False
            return
        try:
            url = f"https://www.googleapis.com/drive/v3/files/{self._lock_file_id}"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}, method="DELETE")
            with urllib.request.urlopen(req, timeout=15):
                pass
            log.info("[Cluster] Liderança liberada voluntariamente no Google Drive.")
        except Exception as e:
            log.debug("[Cluster] Aviso ao liberar lock: %s", e)
        finally:
            with self._lock:
                self._is_leader = False


# Instância global do ClusterLockManager
cluster = ClusterLockManager()
