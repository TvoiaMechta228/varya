"""Сборка MSI для текущего пользователя из полной опубликованной папки."""

import argparse
import hashlib
from pathlib import Path
import subprocess
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parent
NS = "http://schemas.microsoft.com/wix/2006/wi"
UPGRADE = "4639bb5a-6c6d-457c-8575-b048206c527d"
VERSION = "2.2.0"
WIX_URL = "https://github.com/wixtoolset/wix3/releases/download/wix3141rtm/wix314-binaries.zip"
WIX_HASH = "6ac824e1642d6f7277d0ed7ea09411a508f6116ba6fae0aa5f2c7daa2ff43d31"


def element(parent, tag: str, **values):
    """Добавить элемент схемы WiX с экранированием значений XML."""
    return ET.SubElement(parent, "{" + NS + "}" + tag, values)


def identifier(prefix: str, value: str) -> str:
    return prefix + hashlib.sha256(value.casefold().encode()).hexdigest()[:30]


def generate(source: Path, output: Path) -> Path:
    """Включить файлы, постоянные идентификаторы обновления и ярлыки пользователя."""
    source = source.resolve(strict=True)
    if not (source / "Varya.exe").is_file():
        raise ValueError("В опубликованной папке нет Varya.exe")
    ET.register_namespace("", NS)
    root = ET.Element("{" + NS + "}Wix")
    product = element(
        root,
        "Product",
        Id=str(uuid.uuid5(uuid.UUID(UPGRADE), "Product-" + VERSION)),
        Name="Варя",
        Language="1049",
        Codepage="1251",
        Version=VERSION,
        Manufacturer="Varya Project",
        UpgradeCode=UPGRADE,
    )
    element(
        product,
        "Package",
        InstallerVersion="500",
        Compressed="yes",
        InstallScope="perUser",
        InstallPrivileges="limited",
        Platform="x64",
        Description="Варя — локальный голосовой ассистент",
        SummaryCodepage="1251",
    )
    element(
        product,
        "MajorUpgrade",
        DowngradeErrorMessage="Уже установлена более новая версия Вари.",
    )
    element(
        product,
        "MediaTemplate",
        CabinetTemplate="varya{0}.cab",
        EmbedCab="no",
        CompressionLevel="none",
        MaximumUncompressedMediaSize="1500",
    )
    element(product, "Property", Id="ARPNOMODIFY", Value="1")
    element(product, "Property", Id="ARPPRODUCTICON", Value="VaryaIcon")
    element(
        product, "Icon", Id="VaryaIcon", SourceFile=str(source / "Assets/AppIcon.ico")
    )
    directories = element(product, "Directory", Id="TARGETDIR", Name="SourceDir")
    local = element(directories, "Directory", Id="LocalAppDataFolder")
    programs = element(local, "Directory", Id="LocalPrograms", Name="Programs")
    install = element(programs, "Directory", Id="INSTALLFOLDER", Name="Varya")
    menus = element(directories, "Directory", Id="ProgramMenuFolder")
    menu = element(menus, "Directory", Id="VaryaMenu", Name="Варя")
    desktop = element(directories, "Directory", Id="DesktopFolder")
    feature = element(product, "Feature", Id="Complete", Title="Варя", Level="1")
    known = {".": install}
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        if path.is_symlink():
            raise ValueError("Символические ссылки не включаются в установщик")
        relative = path.relative_to(source)
        if len(relative.parts) > 1 and relative.parts[0].casefold() not in {
            "ru",
            "ru-ru",
            "en",
            "en-us",
        }:
            if (source / relative.parts[0] / "Microsoft.ui.xaml.dll.mui").is_file():
                continue
        parent = install
        for index in range(1, len(relative.parts)):
            key = "/".join(relative.parts[:index])
            if key not in known:
                known[key] = element(
                    parent,
                    "Directory",
                    Id=identifier("D", key),
                    Name=relative.parts[index - 1],
                )
            parent = known[key]
        name = relative.as_posix()
        component_id = identifier("C", name)
        component = element(
            parent,
            "Component",
            Id=component_id,
            Win64="yes",
            Guid=str(uuid.uuid5(uuid.UUID(UPGRADE), name.casefold())),
        )
        element(
            component, "File", Id=identifier("F", name), Source=str(path), KeyPath="no"
        )
        element(
            component,
            "RegistryValue",
            Root="HKCU",
            Key="Software\\Varya\\InstallerFiles",
            Name=component_id,
            Type="integer",
            Value="1",
            KeyPath="yes",
        )
        element(feature, "ComponentRef", Id=component_id)
    for directory, name in ((menu, "start"), (desktop, "desktop")):
        component = element(
            directory,
            "Component",
            Id="Shortcut_" + name,
            Guid=str(uuid.uuid5(uuid.UUID(UPGRADE), name)),
            Win64="yes",
        )
        element(
            component,
            "Shortcut",
            Id="Link_" + name,
            Name="Варя",
            Target="[INSTALLFOLDER]Varya.exe",
            WorkingDirectory="INSTALLFOLDER",
            Icon="VaryaIcon",
        )
        element(
            component,
            "RegistryValue",
            Root="HKCU",
            Key="Software\\Varya",
            Name="Shortcut_" + name,
            Type="integer",
            Value="1",
            KeyPath="yes",
        )
        if name == "start":
            element(component, "RemoveFolder", Id="RemoveMenu", On="uninstall")
        element(feature, "ComponentRef", Id="Shortcut_" + name)
    cleanup = element(
        install,
        "Component",
        Id="DirectoryCleanup",
        Win64="yes",
        Guid=str(uuid.uuid5(uuid.UUID(UPGRADE), "cleanup")),
    )
    for key, directory in known.items():
        element(
            cleanup,
            "RemoveFolder",
            Id=identifier("R", key),
            Directory=directory.attrib["Id"],
            On="uninstall",
        )
    element(
        cleanup,
        "RemoveFolder",
        Id="RemoveLocalPrograms",
        Directory="LocalPrograms",
        On="uninstall",
    )
    element(
        cleanup,
        "RegistryValue",
        Root="HKCU",
        Key="Software\\Varya",
        Name="Installed",
        Type="integer",
        Value="1",
        KeyPath="yes",
    )
    element(feature, "ComponentRef", Id="DirectoryCleanup")
    output.mkdir(parents=True, exist_ok=True)
    destination = output / "Varya.wxs"
    ET.indent(root)
    ET.ElementTree(root).write(destination, encoding="utf-8", xml_declaration=True)
    return destination


def toolset() -> Path:
    """Получить переносимые официальные инструменты с проверкой закреплённого SHA-256."""
    directory = ROOT / "tools/wix3"
    if (directory / "light.exe").is_file():
        return directory
    archive = ROOT / "build/wix314-binaries.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(WIX_URL, archive)
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != WIX_HASH:
            raise ValueError("Не совпала контрольная сумма WiX")
    directory.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        if any(
            not (directory / name).resolve().is_relative_to(directory.resolve())
            for name in bundle.namelist()
        ):
            raise ValueError("Недопустимый путь архива")
        bundle.extractall(directory)
    return directory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "dist/Varya")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/installer")
    options = parser.parse_args()
    output = options.output.resolve()
    source = generate(options.source, output)
    tools = toolset()
    obj = output / "Varya.wixobj"
    subprocess.run(
        [
            str(tools / "candle.exe"),
            "-nologo",
            "-arch",
            "x64",
            "-out",
            str(obj),
            str(source),
        ],
        check=True,
    )
    destination = output / ("Varya-" + VERSION + "-x64.msi")
    # ICE91 предупреждает о per-machine установке, которую пакет явно запрещает.
    subprocess.run(
        [
            str(tools / "light.exe"),
            "-nologo",
            "-sice:ICE91",
            "-ct",
            "2",
            "-out",
            str(destination),
            str(obj),
        ],
        check=True,
    )
    with destination.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    (output / "SHA256SUMS.txt").write_text(
        checksum + "  " + destination.name + "\n", encoding="utf-8"
    )
    print("Готово", destination, flush=True)


if __name__ == "__main__":
    main()
