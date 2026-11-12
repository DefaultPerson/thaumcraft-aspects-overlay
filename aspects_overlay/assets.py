"""Read game artwork locally; never bundle or upload Minecraft textures."""
from __future__ import annotations

import io
import json
from pathlib import Path
import zipfile

import numpy as np
from PIL import Image


class Assets:
    def __init__(self, game_dir: Path):
        self.game_dir = Path(game_dir)
        rows = json.loads((Path(__file__).parent / "data/aspects.json").read_text())
        self.info = {row["id"]: row for row in rows}
        texture_paths = {row["texture"]: row["id"] for row in rows if "texture" in row}
        self.icons: dict[str, np.ndarray] = {}
        self.hex: np.ndarray | None = None
        self.table: np.ndarray | None = None
        self.version = ""
        jars = sorted((self.game_dir / "mods").rglob("*.jar"))
        for jar in jars:
            try:
                with zipfile.ZipFile(jar) as archive:
                    names = archive.namelist()
                    if "thaumcraft/api/aspects/Aspect.class" in names:
                        self.version = jar.name
                        self.hex = self._image(archive.read("assets/thaumcraft/textures/gui/hex1.png"))
                        self.table = self._image(archive.read("assets/thaumcraft/textures/gui/guiresearchtable2.png"))
                    for name in names:
                        if not name.endswith(".png"):
                            continue
                        if name in texture_paths:
                            aspect = texture_paths[name]
                        elif "/textures/aspects/" in name:
                            aspect = Path(name).stem.lower()
                        else:
                            continue
                        if aspect in self.info:
                            self.icons[aspect] = self._image(archive.read(name))
            except (zipfile.BadZipFile, KeyError):
                continue
        if self.hex is None:
            raise ValueError("В папке mods не найден Thaumcraft 4 с текстурой hex1.png.")
        if "4.2.3.5" not in self.version:
            raise ValueError("Пока поддерживается Thaumcraft 4.2.3.5; найден " + self.version)
        # Ignore optional aspects that have no artwork in this installed modpack.
        self.recipes = {name: tuple(self.info[name]["components"]) for name in self.icons}
        while True:
            invalid = {name for name, parents in self.recipes.items()
                       if any(parent not in self.recipes for parent in parents)}
            if not invalid:
                break
            for name in invalid:
                del self.recipes[name]
        self.icons = {name: self._tint(icon, self.info[name]["color"])
                      for name, icon in self.icons.items() if name in self.recipes}
        self.dynamic_colors = frozenset(name for name in self.icons
                                       if self.info[name].get("dynamic_color") == "rainbow")

    @staticmethod
    def _image(data: bytes) -> np.ndarray:
        return np.array(Image.open(io.BytesIO(data)).convert("RGBA"))

    @staticmethod
    def _tint(icon: np.ndarray, color: str) -> np.ndarray:
        # Thaumcraft's grayscale aspect artwork is tinted by the aspect color.
        result = icon.copy()
        rgb = np.array([int(color[i:i + 2], 16) for i in (1, 3, 5)]) / 255.0
        result[..., :3] = np.round(icon[..., :3] * rgb).astype(np.uint8)
        return result
