import unittest

import ai


class PackageSmokeTest(unittest.TestCase):
    def test_package_imports_with_version(self) -> None:
        self.assertEqual(ai.__version__, "0.1.0")


if __name__ == "__main__":
    unittest.main()