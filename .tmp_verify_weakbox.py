# -*- coding: utf-8 -*-
"""临时验证脚本3（用完即删）：递归找 23-1 字符串位置 + weak 红框验证。"""
import time

import paramiko

SERVER, USER = '193.112.194.61', 'ubuntu'
PWD = 'j9Uq_BgeWsR^7*4U'

PROBE = r'''
import sys
sys.path.insert(0, '/app')
import json
import fitz
from backend.services import materials_client
from backend.routers.projects import _text_highlight_box, _quote_page_hit

base = '/app/workspace/projects/15/'

def walk(o, path=''):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from walk(v, f'{path}.{k}')
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from walk(v, f'{path}[{i}]')
    elif isinstance(o, str):
        if '23-1 润泽' in o:
            yield path, o

ctx = None
for fn in ('ch1.json', 'ch5.json'):
    d = json.load(open(base + fn))
    for p, s in walk(d):
        print(fn, p, '=>', s[:250])
        if '/src' in p or p.endswith('.src'):
            # 找该块：从路径回溯
            ctx_candidate = None
    # 直接取该 src 所在块的上下文：再走一遍找兄弟 text
    secs = d.get('sections', [])
    for si, s in enumerate(secs):
        for bi, b in enumerate(s.get('blocks', [])):
            for p, val in walk(b, f'sections[{si}].blocks[{bi}]'):
                if p.endswith('.src') and ctx is None:
                    print('block type:', b.get('type'), '| keys:', list(b.keys()))
                    if b.get('type') == 'p':
                        ctx = (b.get('text') or '').strip()
                    else:
                        # 表格块：用 caption + 上一块 p 文本
                        prev = secs[si]['blocks'][bi - 1] if bi > 0 else None
                        ctx = ((prev or {}).get('text') or b.get('caption') or '').strip()
                    print('ctx chosen:', ctx[:250])
    if ctx:
        break

if not ctx:
    raise SystemExit('no ctx')

path = ('润泽科技数据中心基础设施领域不动产投资信托基金（REITs）项目相关证明材料/'
        '五、其他材料/23 原始权益人关于申报材料真实、有效、合规、完备的承诺函/'
        '23-1 润泽科技发展有限公司关于申报材料真实、有效、合规、完备的承诺函.pdf')
doc = fitz.open(base + 'materials/' + path)
n = doc.page_count
print('pages:', n)

hit = _quote_page_hit(doc, n, ctx)
fuzzy = weak = False
ftoks = wtoks = None
if not hit:
    fr = materials_client.fuzzy_quote_page_hit(doc, n, ctx)
    if fr:
        hit, ftoks = fr
        fuzzy = True
        print('fuzzy hit:', hit, 'toks:', ftoks)
if not hit:
    wt = materials_client.weak_topic_tokens(ctx)
    best_w = None
    for i in range(n):
        m = materials_client.weak_topic_match(materials_client.norm_q(doc[i].get_text()), wt)
        if m and (best_w is None or len(m) > len(best_w[1])):
            best_w = (i + 1, m)
    if best_w:
        hit, fuzzy, weak = best_w[0], True, True
        wtoks = best_w[1]
    print('weak hit:', hit, 'matched wtoks:', wtoks)

box = None
if hit and weak:
    box = _text_highlight_box(doc, hit - 1, ctx, wtoks)
elif hit:
    box = _text_highlight_box(doc, hit - 1, ctx, ftoks)
print('weak:', weak, '| hit:', hit, '| hit_box:', box)

if box:
    import io
    from PIL import Image, ImageDraw
    pix = doc[hit - 1].get_pixmap(dpi=120)
    img = Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB')
    x0, y0, x1, y1 = box
    overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
    dr = ImageDraw.Draw(overlay)
    dr.rectangle([x0, y0, x1, y1], fill=(255, 90, 90, 40), outline=(225, 55, 55, 255), width=5)
    img = Image.alpha_composite(img.convert('RGBA'), overlay).convert('RGB')
    img.save('/tmp/weak_box_proof.jpg', 'JPEG', quality=82)
    print('proof saved')
doc.close()
'''


def run(cli, cmd, limit=8000):
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=300)
    out = stdout.read().decode(errors='replace')
    err = stderr.read().decode(errors='replace')
    print(f'$ {cmd[:130]}')
    if out.strip():
        print(out[-limit:])
    if err.strip():
        print('[stderr]', err[-1000:])
    print('---')
    return out


def main():
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    for i in range(6):
        try:
            cli.connect(SERVER, username=USER, password=PWD, timeout=30)
            break
        except Exception as e:
            print('连接重试', i, e)
            time.sleep(10)
    else:
        raise SystemExit('SSH 连接失败')

    sftp = cli.open_sftp()
    with sftp.open('/tmp/probe_weakbox.py', 'w') as f:
        f.write(PROBE)
    sftp.close()
    run(cli, f'echo "{PWD}" | sudo -S docker cp /tmp/probe_weakbox.py reit-app-app-1:/tmp/probe_weakbox.py')
    run(cli, f'echo "{PWD}" | sudo -S docker exec reit-app-app-1 python3 /tmp/probe_weakbox.py')
    run(cli, f'echo "{PWD}" | sudo -S docker cp reit-app-app-1:/tmp/weak_box_proof.jpg /tmp/weak_box_proof.jpg && chmod 644 /tmp/weak_box_proof.jpg')
    sftp = cli.open_sftp()
    try:
        sftp.get('/tmp/weak_box_proof.jpg', '/tmp/weak_box_proof.jpg')
        print('已取回 /tmp/weak_box_proof.jpg')
    except Exception as e:
        print('取回失败:', e)
    sftp.close()
    cli.close()


if __name__ == '__main__':
    main()
