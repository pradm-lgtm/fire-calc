#!/usr/bin/env python3
"""That the test files run every test they contain.

The migrate script runs each file as a script, and a file runs its
`unittest.main()` where that block sits. Appending a class after it means
the class is not defined when main() collects - so three files had tests
that looked present, passed review, and had never run once. Thirteen in
one file, four of them failing.

A static check rather than a count, because counting means running the
whole suite twice.
"""

import ast
import glob
import os
import unittest


class EveryTestActuallyRuns(unittest.TestCase):

    def files(self):
        here = os.path.dirname(os.path.abspath(__file__))
        return sorted(glob.glob(os.path.join(here, "test_*.py")))

    def test_nothing_is_defined_below_the_main_block(self):
        """Anything there is defined too late to be collected."""
        for path in self.files():
            name = os.path.basename(path)
            with self.subTest(file=name):
                tree = ast.parse(open(path).read())
                guard = None
                for node in tree.body:
                    if (isinstance(node, ast.If)
                            and ast.dump(node.test).find("__main__") != -1):
                        guard = node
                if guard is None:
                    continue
                late = [n for n in tree.body
                        if getattr(n, "lineno", 0) > guard.lineno
                        and isinstance(n, (ast.ClassDef, ast.FunctionDef,
                                           ast.Assign))]
                self.assertEqual(
                    [getattr(n, "name", "assignment") for n in late], [],
                    f"{name} defines these below its main block, where "
                    "they will never be collected")

    def test_every_file_has_some_tests_in_it(self):
        for path in self.files():
            name = os.path.basename(path)
            with self.subTest(file=name):
                tree = ast.parse(open(path).read())
                cases = [n for n in ast.walk(tree)
                         if isinstance(n, ast.ClassDef)
                         and any(getattr(b, "attr", getattr(b, "id", ""))
                                 == "TestCase" for b in n.bases)]
                self.assertTrue(cases, f"{name} defines no test case")


if __name__ == "__main__":
    unittest.main(verbosity=2)
