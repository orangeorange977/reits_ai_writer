/**
 * draw.io 嵌入封装（自托管，离线）
 *
 * 用 draw.io embed 模式（iframe + postMessage JSON 协议）画图。
 * 保存时导出 xmlpng：一张 PNG（内嵌源 XML），既能插入 Word、又能重新打开编辑。
 *
 * 对外接口：
 *   DrawioEditor.open(xml|null, onSave)   打开编辑器；保存回调 onSave({png, xml})
 *     - xml：初始加载的 draw.io XML（重新编辑或套模板时传入），空则新建空白图
 *     - png：'data:image/png;base64,...'（xmlpng，内嵌 xml）
 */
const DrawioEditor = (function () {
    'use strict';

    const DRAWIO_URL = '/vendor/drawio/index.html'
        + '?embed=1&proto=json&spin=1&noSaveBtn=0&saveAndExit=0&noExitBtn=0&ui=min&lang=zh';

    const EMPTY_XML = '<mxfile><diagram id="0" name="Page-1">'
        + '<mxGraphModel dx="800" dy="600" grid="1" gridSize="10" guides="1" '
        + 'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
        + 'math="0" shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
        + '</root></mxGraphModel></diagram></mxfile>';

    let _overlay, _iframe, _onSave, _initXml, _latestXml, _done;

    function open(xml, onSave) {
        _onSave = onSave;
        _initXml = xml || EMPTY_XML;
        _latestXml = _initXml;
        _done = false;

        _overlay = document.createElement('div');
        _overlay.className = 'drawio-overlay';
        _overlay.innerHTML = `
            <div class="drawio-modal">
                <iframe class="drawio-frame" src="${DRAWIO_URL}"
                        frameborder="0"></iframe>
            </div>`;
        document.body.appendChild(_overlay);
        _iframe = _overlay.querySelector('.drawio-frame');

        window.addEventListener('message', _onMessage);
    }

    function _post(msg) {
        if (_iframe && _iframe.contentWindow) {
            _iframe.contentWindow.postMessage(JSON.stringify(msg), '*');
        }
    }

    function _close() {
        window.removeEventListener('message', _onMessage);
        if (_overlay) _overlay.remove();
        _overlay = _iframe = null;
    }

    function _onMessage(e) {
        if (!_iframe || e.source !== _iframe.contentWindow) return;
        let msg;
        try { msg = JSON.parse(e.data); } catch (err) { return; }
        if (!msg || !msg.event) return;

        if (msg.event === 'init') {
            // 编辑器就绪：加载初始 XML
            _post({ action: 'load', xml: _initXml, autosave: 1 });
        } else if (msg.event === 'save') {
            // 用户点保存：记住 xml，再请求导出 xmlpng（PNG 内嵌 xml）
            _latestXml = msg.xml || _latestXml;
            _post({ action: 'export', format: 'xmlpng', spinKey: 'export' });
        } else if (msg.event === 'export') {
            // 拿到 PNG（data URI）→ 回调插入，随后关闭
            _done = true;
            const png = msg.data || '';
            const cb = _onSave;
            _close();
            if (cb && png) cb({ png: png, xml: _latestXml });
        } else if (msg.event === 'exit') {
            // 用户退出（未保存则取消）
            if (!_done) _close();
        }
    }

    return { open };
})();
