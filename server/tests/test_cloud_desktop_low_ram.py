import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from computer_desktop import CloudDesktop, _generate_win11_wallpaper


def test_generate_win11_wallpaper_with_icons_and_taskbar(tmp_path):
    wp = tmp_path / "wallpaper.png"
    _generate_win11_wallpaper(wp, 1280, 800)
    assert wp.exists()
    assert wp.stat().st_size > 15_000


def test_desktop_icon_elements_match_painted_layout():
    cd = CloudDesktop()
    icons = cd._desktop_icon_elements()
    assert len(icons) == 8
    assert icons[0]["ref"] == "@b1"
    assert icons[0]["app"] == "chrome"
    assert (icons[0]["x"], icons[0]["y"]) == (60, 66)
    assert icons[4]["ref"] == "@b5"
    assert icons[4]["app"] == "excel"
    assert (icons[4]["x"], icons[4]["y"]) == (174, 66)
