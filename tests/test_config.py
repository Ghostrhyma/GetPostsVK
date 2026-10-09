import unittest

from app.config import ConfigError, load_settings, normalize_database_url

BASE = {"BOT_TOKEN": "tg", "VK_TOKEN": "vk", "DATABASE_URL": "postgresql://u:p@db:5432/bot"}


class NormalizeDatabaseUrl(unittest.TestCase):
    def test_plain_postgres_gets_asyncpg_driver(self):
        self.assertEqual(
            normalize_database_url("postgresql://u:p@h:5432/d"),
            ("postgresql+asyncpg://u:p@h:5432/d", None),
        )
        self.assertEqual(
            normalize_database_url("postgres://u:p@h/d"),
            ("postgresql+asyncpg://u:p@h/d", None),
        )

    def test_asyncpg_url_is_left_alone(self):
        url = "postgresql+asyncpg://u:p@h/d"
        self.assertEqual(normalize_database_url(url), (url, None))

    def test_sslmode_is_converted(self):
        self.assertEqual(
            normalize_database_url("postgresql://u:p@h/d?sslmode=require"),
            ("postgresql+asyncpg://u:p@h/d", "require"),
        )
        self.assertEqual(normalize_database_url("postgresql://h/d?sslmode=verify-full")[1], "verify")
        self.assertEqual(normalize_database_url("postgresql://h/d?sslmode=disable")[1], "off")

    def test_neon_style_url(self):
        url, mode = normalize_database_url(
            "postgresql://u:p@ep-x.neon.tech/d?sslmode=require&channel_binding=require&application_name=bot"
        )
        self.assertEqual(url, "postgresql+asyncpg://u:p@ep-x.neon.tech/d?application_name=bot")
        self.assertEqual(mode, "require")

    def test_sqlite_url_untouched(self):
        url = "sqlite+aiosqlite:///tmp/test.db"
        self.assertEqual(normalize_database_url(url), (url, None))


class LoadSettings(unittest.TestCase):
    def test_defaults(self):
        s = load_settings(BASE)
        self.assertEqual(s.vk_api_host, "api.vk.ru")
        self.assertEqual(s.vk_api_version, "5.199")
        self.assertEqual(s.db_ssl, "off")
        self.assertEqual(s.poll_interval, 60.0)
        self.assertEqual(s.posts_per_check, 10)
        self.assertIsNone(s.admin_id)
        self.assertTrue(s.database_url.startswith("postgresql+asyncpg://"))

    def test_missing_required(self):
        with self.assertRaises(ConfigError) as ctx:
            load_settings({"BOT_TOKEN": "x"})
        self.assertIn("VK_TOKEN", str(ctx.exception))
        self.assertIn("DATABASE_URL", str(ctx.exception))

    def test_blank_values_count_as_missing(self):
        with self.assertRaises(ConfigError):
            load_settings({**BASE, "BOT_TOKEN": "   "})

    def test_legacy_names(self):
        s = load_settings(
            {"BOT_TOKEN": "tg", "TOKEN": "old-vk", "DATABASE_URL": BASE["DATABASE_URL"],
             "GOD_ID": "42", "VERSION": "5.131"}
        )
        self.assertEqual(s.vk_token, "old-vk")
        self.assertEqual(s.admin_id, 42)
        self.assertEqual(s.vk_api_version, "5.131")

    def test_new_names_win_over_legacy(self):
        s = load_settings({**BASE, "TOKEN": "old", "VK_TOKEN": "new"})
        self.assertEqual(s.vk_token, "new")

    def test_ssl_from_url_and_override(self):
        url = "postgresql://u:p@h/d?sslmode=require"
        self.assertEqual(load_settings({**BASE, "DATABASE_URL": url}).db_ssl, "require")
        self.assertEqual(load_settings({**BASE, "DATABASE_URL": url, "DB_SSL": "verify"}).db_ssl, "verify")

    def test_invalid_values(self):
        for bad in (
            {"DB_SSL": "maybe"},
            {"ADMIN_ID": "abc"},
            {"POLL_INTERVAL": "fast"},
            {"POLL_INTERVAL": "1"},
            {"POSTS_PER_CHECK": "0"},
            {"POSTS_PER_CHECK": "500"},
        ):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                load_settings({**BASE, **bad})

    def test_proxy(self):
        self.assertIsNone(load_settings(BASE).telegram_proxy)
        url = "socks5://user:pass@10.0.0.1:1080"
        self.assertEqual(load_settings({**BASE, "PROXY_URL": url}).telegram_proxy, url)
        self.assertEqual(load_settings({**BASE, "TELEGRAM_PROXY": url}).telegram_proxy, url)
        with self.assertRaises(ConfigError):
            load_settings({**BASE, "PROXY_URL": "10.0.0.1:1080"})
        with self.assertRaises(ConfigError):
            load_settings({**BASE, "PROXY_URL": "socks5h://10.0.0.1:1080"})

    def test_api_host_is_cleaned(self):
        self.assertEqual(load_settings({**BASE, "VK_API_HOST": "https://api.vk.ru/"}).vk_api_host, "api.vk.ru")


if __name__ == "__main__":
    unittest.main()
