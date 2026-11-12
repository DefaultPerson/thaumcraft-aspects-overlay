# Thaumcraft Aspects Overlay

**English** · [Русский](README.ru.md)

Recognize Thaumcraft 4 research notes, calculate a connecting scheme and place aspects with one click.

> Built for **LoliLand Techno Magic RPG, Minecraft 1.7.10, Thaumcraft 4.2.3.5, PortProton/Wine + Gamescope, Fedora GNOME Wayland**. Other setups need verification. Live auto-placement requires Linux/Gamescope; Windows/macOS only have an untested offline screenshot mode.

## Demo

https://github.com/user-attachments/assets/b5475065-a7db-4835-a4f7-c1a805c0d7db

45 seconds, 7.2 MB. The recording shows the earlier panel; the current version adds **Решить и расставить** (Solve and place).

## Quick start

Install Git, [uv](https://docs.astral.sh/uv/getting-started/installation/), [Gamescope](https://github.com/ValveSoftware/gamescope) and [Qt X11/xcb libraries](https://doc.qt.io/qt-6/linux-requirements.html). Requires Python 3.12–3.14 and standard mod textures.

```bash
git clone https://github.com/DefaultPerson/thaumcraft-aspects-overlay.git
cd thaumcraft-aspects-overlay
uv sync --frozen
cp config.example.toml config.toml
./run.sh --diagnose
./run.sh
```

Open Minecraft in Gamescope with an unfinished research note before the last two commands. Click **Решить и расставить**; collect the completed note yourself. The interface is in Russian.

- The bot searches the palette, places aspects and checks each placement. **Right-click inside the game to stop.** The desktop mouse remains available.
- Keep the table open and Gamescope visible. After a stopped attempt, click **Решить и расставить** again to account for existing placements.
- Placement spends aspect points and ink; stock is not checked unless an inventory file is configured.

Edit [config.toml](config.example.toml) for your instance path, display, solver budget and exclusions. Install a menu shortcut with `./install-desktop.sh`.

[Full guide](docs/guide.md): compatibility, configuration, controls, offline mode, recognition, troubleshooting and tests.

## Screenshots

<details>
<summary>Controls, solution preview and completed research</summary>

Current one-click controls:

![Current overlay controls](docs/screenshots/one-click-controls.png)

Solution preview with the required aspect counts:

![Solution overlay and aspect costs](docs/screenshots/solution-overlay.png)

Completion confirmed by Minecraft:

![Completed research and confirmation](docs/screenshots/research-completed.png)

The last two screenshots show an earlier panel. All three are from actual runs.

</details>

## Credits

Aspect data adapted from [ThaumcraftResearcher](https://github.com/exaltedo2/ThaumcraftResearcher), with its [MIT license](third_party/ThaumcraftResearcher/LICENSE) and [source record](third_party/ThaumcraftResearcher/SOURCE). Game textures are read locally and are not bundled. Not affiliated with Mojang, Thaumcraft or LoliLand.
