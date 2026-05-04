import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from project_name.main import build_greeting, hello, main


class TestGreetingFunctions(unittest.TestCase):
    def test_hello_uses_default_greeting(self):
        self.assertEqual(hello(), "Hello, World!")

    def test_build_greeting_with_name(self):
        self.assertEqual(build_greeting("Alice"), "Hello, Alice!")

    def test_build_greeting_with_yell(self):
        self.assertEqual(build_greeting("Alice", yell=True), "HELLO, ALICE!")

    def test_build_greeting_rejects_blank_name(self):
        with self.assertRaises(ValueError):
            build_greeting("   ")


class TestMainCLI(unittest.TestCase):
    def run_main(self, argv):
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(argv)

        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_main_prints_default_greeting(self):
        exit_code, stdout, stderr = self.run_main([])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.strip(), "Hello, World!")
        self.assertEqual(stderr, "")

    def test_main_prints_custom_name(self):
        exit_code, stdout, stderr = self.run_main(["--name", "Bob"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.strip(), "Hello, Bob!")
        self.assertEqual(stderr, "")

    def test_main_prints_uppercase_greeting(self):
        exit_code, stdout, stderr = self.run_main(["--name", "Bob", "--yell"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.strip(), "HELLO, BOB!")
        self.assertEqual(stderr, "")

    def test_main_reports_invalid_name(self):
        exit_code, stdout, stderr = self.run_main(["--name", "   "])

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("name must not be empty", stderr)


if __name__ == "__main__":
    unittest.main()
