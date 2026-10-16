import json,statistics
from pathlib import Path
D=Path(__file__).resolve().parent
read=lambda n:json.loads((D/(n+'.json')).read_text(encoding='utf8'))
p=read('pipeline_profile');g=read('gui_usability');sample=sorted(p['5000']['samples'],key=lambda s:s['render_total_ms'])[1]
f=sample['raster_ms']/sample['render_total_ms']
out={'representative_5000':sample,'raster_fraction':f,'zero_cost_raster_theoretical_speedup':1/(1-f),'photoimage_conversion_median_ms':statistics.median(g['photoimage_conversion_ms']),'navigation_handler_median_ms':statistics.median(x['navigation_handler_ms']for x in g['navigation_event_loop']),'pending_timer_median_ms':statistics.median(x['pending_1ms_timer_actual_ms']for x in g['navigation_event_loop']),'rgba8_800_600_mib':800*600*4/(1024**2),'rgba8_1080p_mib':1920*1080*4/(1024**2),'rgba8_4k_mib':3840*2160*4/(1024**2)}
(D/'decision_metrics.json').write_text(json.dumps(out,indent=2),encoding='utf8');print(json.dumps(out,indent=2))
