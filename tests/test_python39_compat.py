import importlib
import unittest


class Python39CompatibilityTest(unittest.TestCase):
    def test_model_modules_import_without_pep604_unions(self):
        importlib.import_module("app.models.organization")
        importlib.import_module("app.models.business_unit")
        importlib.import_module("app.models.lead")


if __name__ == "__main__":
    unittest.main()
