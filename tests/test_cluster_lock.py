"""
Testes unitários para o ClusterLockManager (trava multi-notebook e alta disponibilidade).
"""

import json
import time
import unittest
from unittest.mock import MagicMock, patch

from indomavel.cluster_lock import (
    ClusterLockManager,
    LOCK_FILENAME,
    LOCK_TIMEOUT_SECONDS,
    get_default_machine_id,
)


class TestClusterLock(unittest.TestCase):
    def setUp(self):
        self.mgr_a = ClusterLockManager(machine_id="notebook_alpha_123")
        self.mgr_b = ClusterLockManager(machine_id="notebook_beta_456")

    def tearDown(self):
        self.mgr_a.stop()
        self.mgr_b.stop()

    def test_default_machine_id(self):
        mid = get_default_machine_id()
        self.assertTrue(isinstance(mid, str))
        self.assertGreater(len(mid), 3)

    def test_local_fallback_when_no_drive_token(self):
        """Sem token de Google Drive, máquina atual vira líder local autônomo seguro."""
        with patch("indomavel.google_drive.obter_token_acesso", return_value=(None, "sem token")):
            res = self.mgr_a.evaluate_leadership("Live Teste")
            self.assertTrue(res)
            self.assertTrue(self.mgr_a.is_leader)
            self.assertEqual(self.mgr_a.role_name, "LEADER")

    def test_claim_leadership_when_lock_empty(self):
        """Quando não há lock no Drive, máquina A assume liderança com sucesso."""
        mock_claim = MagicMock()
        with patch("indomavel.google_drive.obter_token_acesso", return_value=("fake_token", None)), \
             patch.object(self.mgr_a, "_obter_lock_drive", return_value=(None, None)), \
             patch.object(self.mgr_a, "_claim_leadership", mock_claim):
            res = self.mgr_a.evaluate_leadership("Live Teste")
            self.assertTrue(res)
            mock_claim.assert_called_once()

    def test_standby_when_another_machine_is_active_leader(self):
        """Quando outra máquina é líder e enviou heartbeat recente, máquina B vira STANDBY."""
        now = time.time()
        lock_data = {
            "leader_machine_id": "notebook_alpha_123",
            "leader_hostname": "NOTEBOOK-ALPHA",
            "heartbeat_epoch": now - 10,  # 10s atrás (ativo!)
            "heartbeat_timestamp": "2026-09-18T12:00:00Z",
            "live_title": "Live Ativa",
        }

        with patch("indomavel.google_drive.obter_token_acesso", return_value=("fake_token", None)), \
             patch.object(self.mgr_b, "_obter_lock_drive", return_value=("file_123", lock_data)):
            res = self.mgr_b.evaluate_leadership("Live Ativa")
            self.assertFalse(res)
            self.assertFalse(self.mgr_b.is_leader)
            self.assertEqual(self.mgr_b.role_name, "STANDBY")
            self.assertEqual(self.mgr_b.current_leader_id, "notebook_alpha_123")

    def test_failover_when_leader_inactive(self):
        """Se o líder anterior estiver inativo há mais de 90s, máquina B assume em failover."""
        now = time.time()
        lock_data = {
            "leader_machine_id": "notebook_alpha_123",
            "leader_hostname": "NOTEBOOK-ALPHA",
            "heartbeat_epoch": now - (LOCK_TIMEOUT_SECONDS + 20),  # 110s atrás (parou!)
            "heartbeat_timestamp": "2026-09-18T10:00:00Z",
            "live_title": "Live Abandonada",
        }

        mock_claim = MagicMock()
        with patch("indomavel.google_drive.obter_token_acesso", return_value=("fake_token", None)), \
             patch.object(self.mgr_b, "_obter_lock_drive", return_value=("file_123", lock_data)), \
             patch.object(self.mgr_b, "_claim_leadership", mock_claim):
            res = self.mgr_b.evaluate_leadership("Live Abandonada")
            self.assertTrue(res)
            mock_claim.assert_called_once()
            # Verifica que informou o líder anterior como origem do failover
            args, kwargs = mock_claim.call_args
            self.assertEqual(kwargs.get("previous_leader_id") or args[4], "notebook_alpha_123")

    def test_voluntary_release_leadership(self):
        """Ao liberar voluntariamente, remove arquivo do Drive e zera liderança."""
        self.mgr_a._is_leader = True
        self.mgr_a._lock_file_id = "lock_file_abc"

        with patch("indomavel.google_drive.obter_token_acesso", return_value=("fake_token", None)), \
             patch("urllib.request.urlopen") as mock_url:
            self.mgr_a.release_leadership()
            self.assertFalse(self.mgr_a.is_leader)
            mock_url.assert_called_once()


if __name__ == "__main__":
    unittest.main()
