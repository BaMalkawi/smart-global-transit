# app.py

import os
import re
import base64
import googlemaps
import openai
import pdfkit
import streamlit as st
import folium

from datetime import datetime
from dotenv import load_dotenv
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium

# -------------------------
# 1) مفتاحّات الـ API
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY")

gmaps = googlemaps.Client(key=GOOGLE_API_KEY)
openai.api_key = OPENAI_API_KEY

# -------------------------
# 2) إعداد الستريمليت
st.set_page_config(page_title="Smart Global Transit", layout="wide")
st.title("🌍 Smart Global Transit")
st.markdown("اكتب جملة مثل: **من عمان إلى العقبة** أو **from Paris to Berlin**")

# المسار المطلق لـِ this file
BASE_DIR   = os.path.dirname(__file__)
BANNER_IMG = os.path.join(BASE_DIR, "jeff-smith-djfewGmvWMg-unsplash.jpg")

# -------------------------
# 3) Session state defaults
defaults = {
    "from_to":       "",
    "mode":          "transit",
    "route_summary": "",
    "instructions":  "",
    "route_map":     None,
    "computed":      False,
    "chat_q":        "",
    "chat_resp":     ""
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# -------------------------
# 4) دوال مساعدة
def extract_locations(text):
    for p in (r"from\s+(.*?)\s+to\s+(.*)", r"من\s+(.*?)\s+(?:إلى|الى)\s+(.*)"):
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(1).strip(), m.group(2).strip()
    return None, None

@st.cache_data(ttl=3600)
def geocode(place):
    res = gmaps.geocode(place)
    if not res:
        return None
    loc = res[0]["geometry"]["location"]
    return loc["lat"], loc["lng"]

@st.cache_data(ttl=300)
def get_dirs(o, d, m):
    return gmaps.directions(
        o, d,
        mode=m,
        departure_time=datetime.now(),
        traffic_model="best_guess"
    )

# -------------------------
# 5) واجهة حساب المسار
with st.form("route_form", clear_on_submit=False):
    frm = st.text_input("📍 من وإلى:", key="from_to",
                        value=st.session_state.from_to,
                        placeholder="من X إلى Y أو from X to Y")
    mode = st.selectbox("🚌 وسيلة التنقل:", ["driving","walking","bicycling","transit"],
                        key="mode")
    if st.form_submit_button("احسب الطريق"):
        # reset
        st.session_state.update({
            "route_summary":"", "instructions":"",
            "route_map":None, "computed":False
        })

        start, end = extract_locations(frm)
        if not start or not end:
            st.warning("⚠️ أدخل جملة مثل: من عمان إلى العقبة أو from X to Y")
        else:
            o = geocode(start)
            d = geocode(end)
            if not o or not d:
                st.error("❌ تعذّر تحديد إحداثيات أحد الموقعين.")
            else:
                dirs = get_dirs(o,d, mode)
                if not dirs and mode=="transit":
                    st.info("⚠️ لا توجد مواصلات عامة، سيتم التحويل إلى السيارة.")
                    dirs = get_dirs(o,d, "driving")
                if not dirs:
                    st.error("❌ لم يُعثر على مسار حتى بالسيارة.")
                else:
                    leg = dirs[0]["legs"][0]
                    dist = leg["distance"]["text"]
                    dur  = leg["duration"]["text"]
                    st.session_state.route_summary = f"**المسافة:** {dist}  \n**المدة الإجمالية:** {dur}"

                    # خطوات HTML → نصّ
                    raw = [re.sub(r"<.*?>","", s.get("html_instructions",""))
                           for s in leg["steps"]]

                    # صياغة GPT
                    prompt = (
                        f"خطوات من {start} إلى {end}:\n"
                        + "\n".join(f"- {r}" for r in raw)
                        + "\n\nأعدّها كخطوات مختصرة ومرقمة بالعربية."
                    )
                    rsp = openai.ChatCompletion.create(
                        model="gpt-3.5-turbo",
                        messages=[{"role":"user","content":prompt}],
                        temperature=0.7, max_tokens=300
                    )
                    st.session_state.instructions = rsp.choices[0].message.content

                    # خارطة overview_polyline
                    pts = googlemaps.convert.decode_polyline(
                        dirs[0]["overview_polyline"]["points"]
                    )
                    m = folium.Map(location=(pts[0]["lat"], pts[0]["lng"]),
                                   zoom_start=12, tiles="CartoDB positron")
                    folium.PolyLine([(p["lat"],p["lng"]) for p in pts],
                                    color="blue", weight=5).add_to(m)
                    folium.Marker((pts[0]["lat"],pts[0]["lng"]),
                                  tooltip="انطلاق",
                                  icon=folium.Icon(color="green",icon="play")).add_to(m)
                    folium.Marker((pts[-1]["lat"],pts[-1]["lng"]),
                                  tooltip="وجهة",
                                  icon=folium.Icon(color="red",icon="stop")).add_to(m)

                    # طبقات POI
                    layers = {
                      "محطات باص":("bus station","blue","bus"),
                      "محطات قطار":("train station","purple","train"),
                      "محطات مترو":("subway station","orange","subway"),
                      "مراكز تسوّق":("shopping mall","cadetblue","shopping-cart"),
                    }
                    for name,(kw,color,icon) in layers.items():
                        fg = folium.FeatureGroup(name=name)
                        mc = MarkerCluster().add_to(fg)
                        for p in gmaps.places_nearby(location=o, keyword=kw, radius=3000).get("results",[]):
                            loc = p["geometry"]["location"]
                            folium.Marker(
                                (loc["lat"],loc["lng"]),
                                popup=p["name"],
                                icon=folium.Icon(color=color,icon=icon,prefix="fa")
                            ).add_to(mc)
                        m.add_child(fg)

                    folium.LayerControl(collapsed=False).add_to(m)
                    st.session_state.route_map = m
                    st.session_state.computed  = True

# -------------------------
# 6) عرض النتائج
if st.session_state.computed:
    st.success("✅ تم حساب المسار")
    st.markdown(st.session_state.route_summary)
    st.markdown("### 🚦 تعليمات الوصول:")
    st.write(st.session_state.instructions)
    st_folium(st.session_state.route_map, width=800, height=500)

    # عرض البانر
    if os.path.exists(BANNER_IMG):
        st.image(BANNER_IMG, use_container_width=True, caption="City Sightseeing")
    else:
        st.warning("⚠️ لم أجد صورة البانر.")

    # توليد PDF (مفهوم اختياري)
    try:
        html = (
            f"<h2>ملخص الرحلة</h2>"
            f"<p>{st.session_state.route_summary.replace(chr(10),'<br>')}</p>"
        )
        pdf = pdfkit.from_string(html, False)
        b64 = base64.b64encode(pdf).decode()
        st.markdown(
          f'<a href="data:application/pdf;base64,{b64}" download="route.pdf">'
          "📄 تحميل ملخص PDF</a>",
          unsafe_allow_html=True
        )
    except OSError:
        st.info("⚠️ لتفعيل تحميل PDF، ثبّت wkhtmltopdf أو حدّد مساره في إعدادات pdfkit.")

# -------------------------
# 7) شات الأماكن السياحية
st.markdown("---")
st.subheader("🧳 اسأل عن أماكن سياحية")
with st.form("chat_form", clear_on_submit=False):
    st.text_input("مثال: أماكن سياحية في إسطنبول", key="chat_q")
    if st.form_submit_button("اسأل الآن") and st.session_state.chat_q:
        resp = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role":"user","content":
                       f"اقترح أفضل 10 أماكن سياحية في {st.session_state.chat_q}"}],
            temperature=0.7, max_tokens=400
        )
        st.session_state.chat_resp = resp.choices[0].message.content

if st.session_state.chat_resp:
    st.markdown("🌟 **أماكن مقترحة:**")
    st.markdown(st.session_state.chat_resp)
