"""Mac ajanının sır koruması (2026-10-28 Mac turu): değer basan dar küme reddedilir, günlük iş geçer."""
import os
import sys
import unittest

import tests.common  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops"))
import mac_agent  # noqa: E402


class MacSirKorumasi(unittest.TestCase):
    def test_reddedilenler(self):
        for k in ("gh auth token", "gh auth status -t", "gh auth status --show-token",
                  "security find-generic-password -s gh:github.com -w", "security dump-keychain",
                  "cat ~/.ssh/ghost_root_sk", "base64 $HOME/.ssh/ghost_runtime", "cat ~/.ssh/id_ed25519",
                  "cat ~/jarvis/secrets.env", "grep TOKEN $HOME/jarvis/.env", "cut -d= -f1 ~/jarvis/secrets.env"):
            self.assertTrue(mac_agent.sir_komutu(k), k)

    def test_serbest_olanlar(self):
        for k in ("gh auth status", "gh api repos/ornek-org/jarvis", "git -C ~/jarvis log --oneline -1",
                  "/opt/homebrew/bin/ssh -o ConnectTimeout=10 -i $HOME/.ssh/ghost_root_sk root@1.2.3.4 \"uptime\"",
                  "scp -q -i $HOME/.ssh/ghost_runtime -o BatchMode=yes ghost@1.2.3.4:/a /b",
                  "cut -s -d= -f1 $HOME/jarvis/secrets.env", "cat ~/.ssh/ghost_vps.pub", "ls -la $HOME/.ssh",
                  "launchctl list | grep jarvis", "curl -s 127.0.0.1:8797/_durum",
                  'security find-generic-password -s "Claude Code-credentials" -w | python3 -c "print(1)"'):
            self.assertFalse(mac_agent.sir_komutu(k), k)


if __name__ == "__main__":
    unittest.main()
