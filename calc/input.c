#include <os.h>
#include "input.h"
typedef struct {
    const t_key *key;
    unsigned code;
    bool held;
    uint32_t repeat;
} Key;
#define K(name, code) {&KEY_NSPIRE_##name, code, false, 0}
/* Modifier edges precede other keys so chords arrive in their original order. */
static Key keys[] = {
    K(SHIFT, 0x10), K(CTRL, 0x11),  K(DOC, 0x12),  K(A, 65),          K(B, 66),
    K(C, 67),       K(D, 68),       K(E, 69),      K(F, 70),          K(G, 71),
    K(H, 72),       K(I, 73),       K(J, 74),      K(K, 75),          K(L, 76),
    K(M, 77),       K(N, 78),       K(O, 79),      K(P, 80),          K(Q, 81),
    K(R, 82),       K(S, 83),       K(T, 84),      K(U, 85),          K(V, 86),
    K(W, 87),       K(X, 88),       K(Y, 89),      K(Z, 90),          K(0, 48),
    K(1, 49),       K(2, 50),       K(3, 51),      K(4, 52),          K(5, 53),
    K(6, 54),       K(7, 55),       K(8, 56),      K(9, 57),          K(SPACE, 0x20),
    K(TAB, 0x09),   K(ESC, 0x1b),   K(DEL, 0x08),  K(ENTER, 0x0d),    K(PERIOD, 0xbe),
    K(COMMA, 0xbc), K(MINUS, 0x6d), K(PLUS, 0x6b), K(MULTIPLY, 0x6a), K(DIVIDE, 0x6f),
    K(UP, 0x26),    K(DOWN, 0x28),  K(LEFT, 0x25), K(RIGHT, 0x27),    K(HOME, 0x24)};
static bool contact, left, right;
static int last_x, last_y;
static uint32_t last_scan;
void mirror_input_scan(MirrorInput *q, uint32_t now, bool reserved) {
    if (!q->enabled)
        return;
    bool reset = q->reset_scan;
    if (!reset && now - last_scan < 5U)
        return;
    last_scan = now;
    q->reset_scan = false;
    touchpad_report_t report = {0};
    bool touch_ok = touchpad_scan(&report) == 0;
    for (unsigned i = 0; i < sizeof(keys) / sizeof(keys[0]); ++i) {
        Key *k = &keys[i];
        bool held;
        if (is_touchpad && k->key->tpad_arrow != TPAD_ARROW_NONE) {
            unsigned arrow = report.arrow;
            held =
                touch_ok && report.pressed &&
                (k->key->tpad_arrow == arrow ||
                 (k->code == 0x26 && (arrow == TPAD_ARROW_UPRIGHT || arrow == TPAD_ARROW_LEFTUP)) ||
                 (k->code == 0x28 &&
                  (arrow == TPAD_ARROW_DOWNLEFT || arrow == TPAD_ARROW_RIGHTDOWN)) ||
                 (k->code == 0x25 &&
                  (arrow == TPAD_ARROW_LEFTUP || arrow == TPAD_ARROW_DOWNLEFT)) ||
                 (k->code == 0x27 &&
                  (arrow == TPAD_ARROW_UPRIGHT || arrow == TPAD_ARROW_RIGHTDOWN)));
        } else
            held = (isKeyPressed)(k->key) != 0;
        if (k->code == 0x0d)
            held = held || isKeyPressed(KEY_NSPIRE_RET);
        if (reserved && (k->code == 0x1b || k->code == 68))
            held = false;
        if (reset) {
            k->held = held;
            k->repeat = now + 350U;
            continue;
        }
        if (held != k->held) {
            input_push(q, INPUT_KEY, held ? 1 : 0, k->code, 0, 0);
            k->held = held;
            k->repeat = now + 350U;
        } else if (held && k->code > 0x12 && (int32_t)(now - k->repeat) >= 0) {
            input_push(q, INPUT_KEY, 3, k->code, 0, 0);
            k->repeat = now + 45U;
        }
    }
    if (touch_ok) {
        const touchpad_info_t *info = touchpad_getinfo();
        if (!reset && report.contact && contact && info && info->width && info->height) {
            int dx = (int)report.x - last_x, dy = last_y - (int)report.y;
            /* A new finger contact or a sensor jump is not cursor movement. */
            if (dx < (int)info->width / 3 && dx > -(int)info->width / 3 &&
                dy < (int)info->height / 3 && dy > -(int)info->height / 3) {
                if (isKeyPressed(KEY_NSPIRE_SCRATCHPAD)) {
                    if (dy)
                        input_push(q, INPUT_WHEEL, 0, 0, 0, -dy * 32767 / info->height);
                } else if (dx || dy)
                    input_push(q, INPUT_MOVE, 0, 0, dx * 32767 / info->width,
                               dy * 32767 / info->height);
            }
        }
        contact = report.contact;
        last_x = report.x;
        last_y = report.y;
        bool click = report.pressed && report.arrow == TPAD_ARROW_CLICK;
        if (!reset && click != left)
            input_push(q, INPUT_BUTTON, click ? 1 : 0, 1, 0, 0);
        left = click;
    } else
        contact = false;
    bool menu = isKeyPressed(KEY_NSPIRE_MENU) != 0;
    if (!reset && menu != right)
        input_push(q, INPUT_BUTTON, menu ? 1 : 0, 2, 0, 0);
    right = menu;
}
