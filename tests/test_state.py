import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import skipUnless

from sbharness.credentials import CredentialStore
from sbharness.economy import WalletService
from sbharness.storage import PersistentStore


class StateTests(unittest.TestCase):
    def test_profile_and_conversation_round_trip_are_protected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.dpapi"
            store = PersistentStore(path)
            store.data["profile"]["name"] = "local-private-profile"
            store.data["conversations"].append({"id": "one", "messages": [{"role": "user", "content": "persist me"}]})
            store.save()
            raw = path.read_bytes()
            if os.name == "nt":
                self.assertNotIn(b"local-private-profile", raw)
                self.assertTrue(raw.startswith(b"SBH-DPAPI-1"))
            loaded = PersistentStore(path)
            self.assertEqual(loaded.data["profile"]["name"], "local-private-profile")
            self.assertEqual(loaded.data["conversations"][0]["messages"][0]["content"], "persist me")

    def test_wallet_allows_negative_balance_and_currency_conversion(self):
        state = {"profile": {"country": "US"}, "wallet": {"balance": "100", "currency": "USD", "ledger": []}}
        saved = []
        wallet = WalletService(state, lambda: saved.append(True))
        wallet.adjust("-114514", "AI 说账户破产")
        self.assertEqual(wallet.balance, Decimal("-114414.00"))
        entry = wallet.change_country("CN")
        self.assertEqual(entry["to"], "CNY")
        self.assertLess(wallet.balance, 0)
        self.assertEqual(wallet.currency, "CNY")
        self.assertEqual(len(state["wallet"]["ledger"]), 2)
        self.assertGreaterEqual(len(saved), 2)

    def test_membership_uses_local_balance_and_thirty_day_expiry(self):
        state = {"profile": {"country": "US"}, "wallet": {"balance": "30", "currency": "USD", "ledger": []}}
        wallet = WalletService(state)
        entry = wallet.purchase("Plus")
        self.assertEqual(entry["tier"], "Plus")
        self.assertEqual(wallet.balance, Decimal("25.00"))
        self.assertEqual(wallet.membership_status()["tier"], "Plus")
        self.assertEqual(len(state["wallet"]["ledger"]), 2)

    @skipUnless(os.name == "nt", "Windows Credential Manager is only available on Windows")
    def test_api_key_persists_in_windows_credential_manager(self):
        credentials = CredentialStore("SBHarness.Test")
        profile_id = f"credential-test-{os.getpid()}-{id(self)}"
        try:
            credentials.set(profile_id, "test-key-never-a-real-secret")
            self.assertEqual(credentials.get(profile_id), "test-key-never-a-real-secret")
        finally:
            credentials.set(profile_id, "")


if __name__ == "__main__":
    unittest.main()
