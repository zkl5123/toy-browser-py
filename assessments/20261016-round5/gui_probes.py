import sys,json
from pathlib import Path
D=Path(__file__).resolve().parent;sys.path.insert(0,str(D.parents[1]));from toy_browser.app import gui; from toy_browser.document import dom; from toy_browser.app import browser
def text(n):return (getattr(n,'text','') or '')+''.join(text(c) for c in n.children)
html="<html><body><div id='out'>BEFORE</div><script>document.getElementById('out').textContent='AFTER';</script></body></html>"
p=gui.PageInstance();p.load_html(html,w=400,h=200);browser.write_png(p.bitmap,str(D/'gui_script.png'))
tree=p._dom_tree
out={'gui_inline_script_text':tree.find_by_id('out').text_content(),'expected':'AFTER','long_scroll':{}}
p.load_html('<html><body><div style="height:17000px;background:#ddd">long</div><div id="bottom">BOTTOM</div></body></html>',w=80,h=100)
p.scroll_to(999999);out['long_scroll']={'content_height':p.content_height,'max_offset':p.scroll_max(),'actual_offset':p.yoffset,'expected_content_at_least':17000}
import tkinter as tk
root=tk.Tk();root.withdraw();app=gui.TabBrowser(root);root.update();out['tabs_initial']=len(app.tabs);app._new_tab();root.update();out['tabs_after_new']=len(app.tabs);app._close_tab();root.update();out['tabs_after_close']=len(app.tabs);root.destroy()
try:
 p=gui.PageInstance();p.load_url('about:blank',w=100,h=100);p.load_url('about:bookmarks',w=100,h=100);p.go_back();out['back_url']=p.url;p.go_forward();out['forward_url']=p.url
except Exception as e:out['about_navigation_error']=repr(e)
(D/'gui_results.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8');print(out)
