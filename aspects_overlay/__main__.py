from __future__ import annotations

from dataclasses import asdict
import json
import logging
import os
import sys

from .config import parse_args


def main(argv=None):
    args = parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(args.output / "overlay.log"), logging.StreamHandler()])
    try:
        from .assets import Assets
        if args.image:
            target = None
            assets = Assets(args.game_dir)
        else:
            if sys.platform != 'linux':
                raise ValueError('Live overlay requires Linux/X11. Use --image and --game-dir for offline solving.')
            from .capture import discover
            target = discover(args.display, args.game_dir, args.window_id)
            if args.diagnose:
                print(json.dumps(asdict(target), default=str, indent=2))
                return 0
            assets = Assets(target.game_dir)
        inventory = json.loads(args.inventory.read_text()) if args.inventory else None
        exclusions = set(filter(None, args.exclude.lower().split(",")))
        if exclusions - assets.recipes.keys():
            raise ValueError("Неизвестные аспекты в --exclude: " + ", ".join(sorted(exclusions-assets.recipes.keys())))
        if exclusions:
            if inventory is None:
                # Large values are explicitly only a way to disable specified aspects.
                inventory = {name: 99999 for name in assets.recipes}
            inventory.update({name: 0 for name in exclusions})
        if args.image:
            import cv2
            from .recognition import recognize
            from .solver import solve, validate_solution
            image = cv2.imread(str(args.image))
            if image is None:
                raise ValueError("Не удалось прочитать снимок.")
            board = recognize(image, assets)
            result = solve(board.cells, board.anchors, assets.recipes, inventory, args.timeout)
            valid, reason = validate_solution(board.cells, board.anchors, assets.recipes, result.placements, inventory)
            report = {"board": board.to_dict(), "status": result.status, "valid": valid,
                      "message": reason, "elapsed_s": result.elapsed_s,
                      "placements": [{"q": q, "r": r, "aspect": a} for (q, r), a in sorted(result.placements.items())]}
            path = args.output / "solution.json"
            path.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
            return 0 if valid and result.status in ("optimal", "feasible") else 2
        os.environ["DISPLAY"] = target.display
        os.environ["QT_QPA_PLATFORM"] = "xcb"
        os.environ["QT_SCALE_FACTOR"] = "1"
        os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"
        # Keep the lock open for the whole application lifetime. No PID-based kill.
        import fcntl
        lock = (args.output / "overlay.lock").open("a+")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Оверлей уже запущен. Используйте кнопки на панели сверху слева в игре.")
            return 0
        from .app import run
        return run(target, assets, args.timeout, inventory, args.output, args.inventory is not None)
    except Exception as exc:
        logging.exception("Cannot run overlay")
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
