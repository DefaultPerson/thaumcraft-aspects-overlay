from pathlib import Path

import pytest

from aspects_overlay.config import parse_args
from aspects_overlay.capture import _instance_dir


def test_config_paths_and_command_line_precedence(tmp_path, monkeypatch):
    config = tmp_path/'profile.toml'
    config.write_text('game_dir="instance"\noutput="logs"\ntimeout=20\nexclude=["terra"]\ndisplay=":2"\n')
    monkeypatch.chdir(tmp_path.parent)
    args = parse_args(['--config', str(config), '--timeout', '4', '--exclude', ' Aer, ORDO '])
    assert args.game_dir == tmp_path/'instance'
    assert args.output == tmp_path/'logs'
    assert args.timeout == 4 and args.exclude == 'aer,ordo' and args.display == ':2'


def test_local_config_is_optional_and_cli_can_clear_exclusions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert parse_args([]).timeout == 8
    Path('config.toml').write_text('timeout=12\nexclude=["terra"]\n')
    assert parse_args([]).timeout == 12
    assert parse_args(['--exclude', '']).exclude == ''


@pytest.mark.parametrize('content', ['timeout=true', 'timeout=nan', 'timeout=70',
                                    'game_dir=3', 'exclude="terra"', 'typo=1',
                                    'window_id="no"', 'window_id="0x0"', 'display=""'])
def test_invalid_config_is_rejected_before_capture(tmp_path, content):
    path = tmp_path/'bad.toml'
    path.write_text(content)
    with pytest.raises(SystemExit) as exc:
        parse_args(['--config', str(path)])
    assert exc.value.code == 2


def test_explicit_window_and_offline_modes_validate_required_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        parse_args(['--window-id', '0x123'])
    with pytest.raises(SystemExit):
        parse_args(['--image', 'note.png'])
    args = parse_args(['--display', ':3', '--game-dir', '~/Minecraft', '--window-id', '0x123'])
    assert args.window_id == 0x123 and args.game_dir == Path.home()/'Minecraft'


def test_launchers_with_relative_game_directory_or_no_game_argument(tmp_path):
    instance = tmp_path/'instance'
    (instance/'mods').mkdir(parents=True)
    assert _instance_dir(['--gameDir', 'instance'], tmp_path) == instance
    assert _instance_dir([], instance) == instance
    assert _instance_dir([], tmp_path, instance) == instance
    assert _instance_dir([], instance, tmp_path/'missing') is None


def test_non_linux_live_mode_reports_limitation_before_x11_import(tmp_path, monkeypatch, capsys):
    from aspects_overlay import __main__ as cli
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.sys, 'platform', 'win32')
    assert cli.main([]) == 1
    assert 'Live overlay requires Linux/X11' in capsys.readouterr().err
