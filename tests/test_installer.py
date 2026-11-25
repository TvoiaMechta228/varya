"""Установщик содержит все файлы и ограничен текущим пользователем Windows."""

import xml.etree.ElementTree as ET

import build_installer


def test_installer_preserves_paths_and_per_user_scope(tmp_path):
    source = tmp_path / "published"
    (source / "models").mkdir(parents=True)
    (source / "Varya.exe").touch()
    (source / "models/модель.bin").write_bytes(b"model")
    path = build_installer.generate(source, tmp_path / "installer")
    xml = ET.parse(path)
    ns = {"w": build_installer.NS}
    package = xml.find(".//w:Package", ns)
    assert package.attrib["InstallScope"] == "perUser"
    assert package.attrib["InstallPrivileges"] == "limited"
    files = xml.findall(".//w:File", ns)
    assert {item.attrib["Source"] for item in files} == {
        str(source / "Varya.exe"),
        str(source / "models/модель.bin"),
    }
    assert all(
        value.attrib["Root"] == "HKCU"
        for value in xml.findall(".//w:RegistryValue", ns)
    )
    assert len(xml.findall(".//w:Shortcut", ns)) == 2
    assert xml.find(".//w:MajorUpgrade", ns) is not None


def test_rebuild_keeps_product_identity_and_supported_mui(tmp_path):
    source = tmp_path / "app"
    source.mkdir()
    (source / "Varya.exe").touch()
    for culture in ("ru-RU", "en-us", "gd-gb"):
        (source / culture).mkdir()
        (source / culture / "Microsoft.ui.xaml.dll.mui").touch()
    ns = {"w": build_installer.NS}
    first = ET.parse(build_installer.generate(source, tmp_path / "first"))
    second = ET.parse(build_installer.generate(source, tmp_path / "second"))
    assert (
        first.find("w:Product", ns).attrib["Id"]
        == second.find("w:Product", ns).attrib["Id"]
    )
    paths = [item.attrib["Source"] for item in first.findall(".//w:File", ns)]
    assert str(source / "ru-RU/Microsoft.ui.xaml.dll.mui") in paths
    assert str(source / "en-us/Microsoft.ui.xaml.dll.mui") in paths
    assert str(source / "gd-gb/Microsoft.ui.xaml.dll.mui") not in paths
