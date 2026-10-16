"""Program-driven GUI actions through TabBrowser handlers; no manual UI claim."""
import json,sys,types
from pathlib import Path
D=Path(__file__).resolve().parent;sys.path.insert(0,str(D.parents[1]))
import tkinter as tk
from toy_browser.app import gui
root=tk.Tk();root.withdraw();app=gui.TabBrowser(root);out={}
try:
 app._open_url('about:version');app._open_url('about:bookmarks');app._back();out['back_url']=app.tabs[app.current].url;app._forward();out['forward_url']=app.tabs[app.current].url
 page=app.tabs[app.current]
 html='<!DOCTYPE html><html><body style="margin:0"><button id="b" style="width:100px;height:30px" onclick="document.getElementById(\'out\').textContent=\'CLICKED\'">Go</button><div id="out">SAFE</div></body></html>'
 (D/'gui_click.html').write_text(html,encoding='utf8')
 page.load_html(html,w=400,h=200);app._refresh_canvas(app.current);root.update()
 def find(b):
  if b.dom is not None and b.dom.attr('id')=='b':return b
  for c in b.children:
   r=find(c)
   if r is not None:return r
 bx=find(page._layout_root);out['button_rect']=[bx.rect.x,bx.rect.y,bx.rect.w,bx.rect.h]
 hit=page.hit_test_at(int(bx.rect.x+5),int(bx.rect.y+5));node=page._node_index_to_dom(page._dom_tree,hit) if hit is not None else None
 out['hit_id']=node.attr('id') if node is not None else None
 app._click_dispatch(types.SimpleNamespace(x=int(bx.rect.x+5),y=int(bx.rect.y+5)))
 out['onclick_text']=page._dom_tree.find_by_id('out').text_content()
 out['onclick_expected']='CLICKED'
finally:root.destroy()
(D/'gui_workflow.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8');print(out)
