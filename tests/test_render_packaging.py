import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RenderPackagingTests(unittest.TestCase):
    def test_render_files_exist_without_committed_secret(self):
        render_yaml = ROOT / "render.yaml"
        gitignore = ROOT / ".gitignore"
        env_example = ROOT / ".env.example"

        self.assertTrue(render_yaml.exists())
        self.assertTrue(gitignore.exists())
        self.assertTrue(env_example.exists())
        self.assertIn("uvicorn bot:app", render_yaml.read_text(encoding="utf-8"))
        self.assertIn("MISTRAL_API_KEY", env_example.read_text(encoding="utf-8"))

        for path in ROOT.rglob("*"):
            if path.is_file() and path.suffix not in {".pyc", ".zip"} and "tests" not in path.parts and ".git" not in path.parts and "__pycache__" not in path.parts:
                text = path.read_text(encoding="utf-8", errors="ignore")
                self.assertNotRegex(text, r"MISTRAL_API_KEY\s*=\s*['\"][^'\"]+['\"]")


if __name__ == "__main__":
    unittest.main()
