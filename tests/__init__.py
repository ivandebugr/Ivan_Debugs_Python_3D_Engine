# Marks tests/ as a regular package so `python3 -m tests.smoke_test_harness`
# resolves this directory. Without it, an unrelated `tests` package installed in
# site-packages (a regular package) shadows this namespace directory and `-m`
# fails with "No module named tests.smoke_test_harness".
