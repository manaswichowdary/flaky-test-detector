import flakydetect
from flakydetect import cli


def test_package_imports():
    assert flakydetect.__version__


def test_cli_help_exits_zero(capsys):
    assert cli.main([]) == 0
    assert "flakydetect" in capsys.readouterr().out
