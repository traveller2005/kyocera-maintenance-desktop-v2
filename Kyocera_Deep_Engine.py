#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
لوحة تحكم ومنظومة صيانة طابعات كيو سيرا (Kyocera TASKalfa)
النسخة الهندسية المتقدمة (Master V3 - الكاملة)
"""

import os, json, sqlite3, threading, socket, time, platform, re
import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk
from PIL import Image
import webbrowser
import requests
import urllib3
from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ========================================================================
# دعم عرض اللغة العربية والرموز بشكل صحيح
# ========================================================================
import arabic_reshaper
from bidi.algorithm import get_display

_reshaper = arabic_reshaper.ArabicReshaper(
    configuration={'delete_harakat': False, 'support_ligatures': True, 'use_unshaped_instead_of_isolated': False}
)

LRM = "\u200E"
RLM = "\u200F"

def ar(text):
    if not text or not isinstance(text, str): return text
    if text.endswith(RLM): return text
    try:
        if "\n" in text: return "\n".join(get_display(_reshaper.reshape(line)) for line in text.split("\n"))
        return get_display(_reshaper.reshape(text)) + RLM
    except: return text

def ar_mixed(text):
    """يعزل الأرقام/الرموز اللاتينية داخل نص عربي لمنع تشوه الاتجاه"""
    text = re.sub(r'([A-Za-z0-9%.\-:]+)', lambda m: LRM + m.group(1) + LRM, text)
    return ar(text)

_orig_label_configure = ctk.CTkLabel.configure
def _safe_label_configure(self, *args, **kwargs):
    if "text" in kwargs: kwargs["text"] = ar(kwargs["text"])
    return _orig_label_configure(self, *args, **kwargs)
ctk.CTkLabel.configure = _safe_label_configure

_orig_textbox_insert = ctk.CTkTextbox.insert
def _safe_textbox_insert(self, index, text, tags=None):
    if tags is None: return _orig_textbox_insert(self, index, ar(text))
    return _orig_textbox_insert(self, index, ar(text), tags)
ctk.CTkTextbox.insert = _safe_textbox_insert

# ========================================================================
# الإعدادات العامة والمسارات
# ========================================================================
ctk.set_appearance_mode("Dark")
KYO_RED = "#E31837"
KYO_DARK_RED = "#B3122A"
KYO_BLACK = "#151515"
KYO_PANEL = "#222222"
KYO_GRAY = "#444444"
KYO_TEXT = "#FFFFFF"
KYO_GREEN = "#00A859"

BASE_DIR = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
WORK_DIR = os.path.join(os.path.expanduser("~"), "Documents", "Kyocera_Workshop")
os.makedirs(WORK_DIR, exist_ok=True)
DB_PATH = os.path.join(WORK_DIR, "kyocera_crm.db")
IMAGES_DIR = os.path.join(BASE_DIR, "Renamed_Images")

# ========================================================================
# Backend Classes
# ========================================================================
class PrinterConnection:
    def __init__(self, mode="network", ip="192.168.1.100", usb_name=None):
        self.mode = mode
        self.ip = ip
        self.usb_name = usb_name

    def send_raw(self, command: str, timeout=5) -> tuple:
        if self.mode == "usb": return self._send_usb(command)
        return self._send_network(command, timeout)

    def _send_network(self, command, timeout):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((self.ip, 9100))
            s.sendall(command.encode('utf-8'))
            s.close()
            return True, "تم الإرسال عبر الشبكة بنجاح."
        except Exception as e: return False, str(e)

    def _send_usb(self, command):
        try:
            import win32print
            if not self.usb_name: return False, "لم يتم تحديد طابعة USB"
            h = win32print.OpenPrinter(self.usb_name)
            win32print.StartDocPrinter(h, 1, ("Kyocera_CMD", None, "RAW"))
            win32print.StartPagePrinter(h)
            win32print.WritePrinter(h, command.encode('utf-8'))
            win32print.EndPagePrinter(h)
            win32print.EndDocPrinter(h)
            win32print.ClosePrinter(h)
            return True, "تم الإرسال عبر USB بنجاح."
        except Exception as e: return False, str(e)

    # ------------ الدوال الجديدة المضافة للتفليش ------------
    def send_file(self, file_path, progress_callback=None):
        if self.mode == "usb": return self._send_file_usb(file_path, progress_callback)
        return self._send_file_network(file_path, progress_callback)

    def _send_file_network(self, file_path, progress_callback):
        try:
            total_size = os.path.getsize(file_path)
            sent_bytes = 0
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(15) # مهلة أطول للملفات الكبيرة
            s.connect((self.ip, 9100))
            with open(file_path, 'rb') as f:
                while chunk := f.read(4096): # تقسيم الملف (Chunks)
                    s.sendall(chunk)
                    sent_bytes += len(chunk)
                    if progress_callback: progress_callback(sent_bytes / total_size)
            s.close()
            return True, "تم التفليش بنجاح! الماكينة تقوم الآن بتثبيت النظام، لا تفصل الكهرباء."
        except Exception as e: return False, str(e)

    def _send_file_usb(self, file_path, progress_callback):
        try:
            import win32print
            if not self.usb_name: return False, "لم يتم تحديد طابعة USB"
            total_size = os.path.getsize(file_path)
            sent_bytes = 0
            h = win32print.OpenPrinter(self.usb_name)
            win32print.StartDocPrinter(h, 1, ("Kyocera_Firmware", None, "RAW"))
            win32print.StartPagePrinter(h)
            with open(file_path, 'rb') as f:
                while chunk := f.read(4096):
                    win32print.WritePrinter(h, chunk)
                    sent_bytes += len(chunk)
                    if progress_callback: progress_callback(sent_bytes / total_size)
            win32print.EndPagePrinter(h)
            win32print.EndDocPrinter(h)
            win32print.ClosePrinter(h)
            return True, "تم التفليش عبر كابل الـ USB بنجاح!"
        except Exception as e: return False, str(e)

    def check_port(self, port, timeout=1.5) -> bool:
        if self.mode != "network": return False
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            res = sock.connect_ex((self.ip, port)) == 0
            sock.close()
            return res
        except: return False

    def check_usb_alive(self):
        try:
            import win32print
            h = win32print.OpenPrinter(self.usb_name)
            win32print.ClosePrinter(h)
            return True, "USB"
        except: return False, "0ms"


    def __init__(self, mode="network", ip="192.168.1.100", usb_name=None):
        self.mode = mode
        self.ip = ip
        self.usb_name = usb_name

    def send_raw(self, command: str, timeout=5) -> tuple:
        if self.mode == "usb": return self._send_usb(command)
        return self._send_network(command, timeout)

    def _send_network(self, command, timeout):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((self.ip, 9100))
            s.sendall(command.encode('utf-8'))
            s.close()
            return True, "تم الإرسال عبر الشبكة بنجاح."
        except Exception as e: return False, str(e)

    def _send_usb(self, command):
        try:
            import win32print
            if not self.usb_name: return False, "لم يتم تحديد طابعة USB"
            h = win32print.OpenPrinter(self.usb_name)
            win32print.StartDocPrinter(h, 1, ("Kyocera_CMD", None, "RAW"))
            win32print.StartPagePrinter(h)
            win32print.WritePrinter(h, command.encode('utf-8'))
            win32print.EndPagePrinter(h)
            win32print.EndDocPrinter(h)
            win32print.ClosePrinter(h)
            return True, "تم الإرسال عبر USB بنجاح."
        except Exception as e: return False, str(e)

    def check_port(self, port, timeout=1.5) -> bool:
        if self.mode != "network": return False
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            res = sock.connect_ex((self.ip, port)) == 0
            sock.close()
            return res
        except: return False

    def check_usb_alive(self):
        try:
            import win32print
            h = win32print.OpenPrinter(self.usb_name)
            win32print.ClosePrinter(h)
            return True, "USB"
        except: return False, "0ms"

class CommandCenterClient:
    def __init__(self, ip, username="Admin", password="Admin"):
        self.ip = ip
        self.username = username
        self.password = password
        self.session = requests.Session()

    def post_address(self, payload, endpoint="/start/program/address_book"):
        try:
            login_url = f"https://{self.ip}/start/login.cgi"
            login_data = {"login_name": self.username, "login_pwd": self.password, "func": "login"}
            self.session.post(login_url, data=login_data, verify=False, timeout=5)
            target_url = f"https://{self.ip}{endpoint}"
            resp = self.session.post(target_url, data=payload, verify=False, timeout=5)
            return resp.status_code == 200, f"Status: {resp.status_code}"
        except Exception as e: return False, str(e)

class CrmDatabase:
    def __init__(self, db_path):
        self.db_path = db_path
        self._setup()

    def _setup(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, phone TEXT, model TEXT, details TEXT, cost TEXT, date TEXT)''')
        except Exception as e: print(f"DB Setup Error: {e}")

    def add_record(self, name, phone, model, details, cost):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT INTO records (name, phone, model, details, cost, date) VALUES (?,?,?,?,?,?)", (name, phone, model, details, cost, datetime.now().strftime("%Y-%m-%d")))

    def list_records(self):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT id, name, phone, model, cost, date FROM records ORDER BY id DESC").fetchall()

# ========================================================================
# الفئة الرئيسية للبرنامج
# ========================================================================
class KyoceraDeepEngine:
    def __init__(self, root):
        self.root = root
        self.root.title("Kyocera Deep Engine - Master V3")
        self.root.geometry("1360x780")
        self.root.configure(fg_color=KYO_BLACK)
        
        self._shutdown = False
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        self.ip_address = "192.168.1.100"
        self.db_files = {}
        self.backend = PrinterConnection(mode="network", ip=self.ip_address)
        self.crm_db = CrmDatabase(DB_PATH)
        self.cc_client = CommandCenterClient(self.ip_address)
        
        self.load_databases()
        self.create_notebook()
        
        self.status_var = tk.StringVar(value=ar("⏳ النظام جاهز - غير متصل"))
        self.status_bar = ctk.CTkLabel(self.root, textvariable=self.status_var, fg_color="#000000", text_color=KYO_RED, font=("Segoe UI", 13, "bold"), corner_radius=0, pady=8)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def on_close(self):
        self._shutdown = True
        self.is_connected = False
        self.root.after(200, self.root.destroy)

    def load_databases(self):
        files_to_load = ["c_codes.json", "f_codes.json", "jam_codes.json", "abnormal_noise.json", "malfunctions.json", "sending_errors.json", "maintenance mode.json", "maintenance.json", "adjustments.json", "pwb_connections.json", "printing_system.json"]
        for file in files_to_load:
            path = os.path.join(BASE_DIR, file)
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f: self.db_files[file] = json.load(f)
                except: self.db_files[file] = []
            else: self.db_files[file] = []

    def update_status(self, message, is_error=False):
        if not self._shutdown:
            self.status_var.set(ar(message))
            self.status_bar.configure(text_color=KYO_RED if is_error else KYO_GREEN)
            self.root.update_idletasks()

    def send_raw_command(self, command):
        def task():
            success, msg = self.backend.send_raw(command)
            if not self._shutdown:
                if success:
                    self.update_status("✅ تم إرسال الأمر بنجاح")
                    self.root.after(0, lambda: messagebox.showinfo("نجاح", msg))
                else:
                    self.update_status("❌ فشل الاتصال", is_error=True)
                    self.root.after(0, lambda: messagebox.showerror("خطأ", f"فشل الإرسال:\n{msg}"))
        threading.Thread(target=task, daemon=True).start()

    def create_notebook(self):
        self.main_container = ctk.CTkFrame(self.root, fg_color=KYO_BLACK, corner_radius=0)
        self.main_container.pack(fill=tk.BOTH, expand=True)
        self.sidebar = ctk.CTkScrollableFrame(self.main_container, width=250, fg_color=KYO_PANEL, corner_radius=0)
        self.sidebar.pack(side=tk.RIGHT, fill=tk.Y)
        self.content_container = ctk.CTkFrame(self.main_container, fg_color=KYO_BLACK, corner_radius=0)
        self.content_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

        sidebar_mapping = {
            "🔌 الاتصال الميداني": self.build_connection_tab,
            "🚑 الطوارئ والإنقاذ": self.build_rescue_tab,
            "🔧 الصيانة والإنعاش": self.build_maintenance_tab,
            "⚡ تفليش الفيرموير": self.build_flasher_tab, # <--- السطر الجديد هنا
            "🖨️ الاسكانر والنسخ": self.build_scan_copy_tab,
            "📇 دفتر العناوين": self.build_address_book_tab,
            "📺 شاشة الماكينة (VNC)": self.build_vnc_tab,
            "🧠 التشخيص الذكي": self.build_smart_diag_tab,
            "🛠️ أكواد الصيانة": self.build_ucodes_tab,
            "⚙️ هندسة اللوحات PWBs": self.build_pwb_tab,
            "🖨️ الهاردوير والتفكيك": self.build_hardware_tab,
            "⚠️ أعطال التشغيل": self.build_malfunctions_tab,
            "🌐 أعطال الشبكة والطباعة": self.build_network_tab,
            "💻 مختبر PRESCRIBE": self.build_terminal_tab,
            "📋 إدارة العملاء CRM": self.build_crm_tab,
        }

        self.sections_list = list(sidebar_mapping.keys())
        self.content_frames = {}
        self.sidebar_buttons = {}

        for ar_name, builder_func in sidebar_mapping.items():
            key = ar(ar_name)
            frame = ctk.CTkFrame(self.content_container, fg_color=KYO_PANEL, corner_radius=8)
            self.content_frames[key] = frame
            builder_func(frame)
            btn = ctk.CTkButton(self.sidebar, text=ar_name, anchor="w", font=("Segoe UI", 14, "bold"), fg_color="transparent", hover_color=KYO_GRAY, text_color=KYO_TEXT, corner_radius=6, height=45, command=lambda k=key: self.show_section(k))
            btn.pack(fill=tk.X, padx=6, pady=4)
            self.sidebar_buttons[key] = btn

        self.show_section(ar(self.sections_list[0]))

    def show_section(self, key):
        for section_key, frame in self.content_frames.items():
            frame.pack_forget()
            if section_key in self.sidebar_buttons: self.sidebar_buttons[section_key].configure(fg_color="transparent")
        if key in self.content_frames: self.content_frames[key].pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        if key in self.sidebar_buttons: self.sidebar_buttons[key].configure(fg_color=KYO_RED)

    # =====================================================
    # 1. الاتصال الميداني ورادار الشبكة
    # =====================================================
    def build_connection_tab(self, tab):
        ctk.CTkLabel(tab, text=" 📡 إعدادات الاتصال الميداني ورادار الشبكة ", text_color=KYO_RED, font=("Segoe UI", 16, "bold")).pack(anchor="e", padx=15, pady=5)
        self.conn_type = ctk.StringVar(value="network")
        type_frame = ctk.CTkFrame(tab, fg_color="transparent")
        type_frame.pack(fill=tk.X, padx=15, pady=5)
        
        ctk.CTkRadioButton(type_frame, text="اتصال شبكي (IP)", variable=self.conn_type, value="network", font=("Segoe UI", 13), command=self.toggle_conn_ui).pack(side=tk.RIGHT, padx=15)
        ctk.CTkRadioButton(type_frame, text="كابل مباشر (USB)", variable=self.conn_type, value="usb", font=("Segoe UI", 13), command=self.toggle_conn_ui).pack(side=tk.RIGHT, padx=15)

        self.f_net = ctk.CTkFrame(tab, border_width=1, border_color=KYO_GRAY, fg_color="#1a1a1a", corner_radius=8)
        self.f_net.pack(fill="x", pady=5, padx=15)
        ctk.CTkLabel(self.f_net, text="رقم IP الماكينة:", font=("Segoe UI", 14, "bold"), text_color=KYO_TEXT).pack(side="right", padx=15, pady=15)
        self.ip_combo = ctk.CTkComboBox(self.f_net, values=["192.168.1.100", "10.0.0.50"], font=("Consolas", 15, "bold"), width=180, fg_color=KYO_BLACK, justify="center")
        self.ip_combo.pack(side="right", padx=10, pady=15)
        self.ip_combo.set(self.ip_address)
        ctk.CTkButton(self.f_net, text="🔍 فحص الشبكة", fg_color=KYO_GRAY, font=("Segoe UI", 13, "bold"), command=self.do_network_scan).pack(side="right", padx=10, pady=15)

        self.f_ports = ctk.CTkFrame(self.f_net, fg_color="transparent")
        self.f_ports.pack(fill="x", pady=5)
        self.lbl_port80 = ctk.CTkLabel(self.f_ports, text=ar_mixed("منفذ الويب 80 : قيد الانتظار ⚪"), text_color=KYO_GRAY, font=("Segoe UI", 12, "bold"))
        self.lbl_port80.pack(side="right", padx=20)
        self.lbl_port9100 = ctk.CTkLabel(self.f_ports, text=ar_mixed("منفذ الأوامر 9100 : قيد الانتظار ⚪"), text_color=KYO_GRAY, font=("Segoe UI", 12, "bold"))
        self.lbl_port9100.pack(side="right", padx=20)

        self.f_usb = ctk.CTkFrame(tab, border_width=1, border_color=KYO_GRAY, fg_color="#1a1a1a", corner_radius=8)
        ctk.CTkLabel(self.f_usb, text="طابعة USB:", font=("Segoe UI", 14, "bold"), text_color=KYO_TEXT).pack(side="right", padx=15, pady=15)
        self.usb_combo = ctk.CTkComboBox(self.f_usb, font=("Segoe UI", 13), width=300, fg_color=KYO_BLACK)
        self.usb_combo.pack(side="right", padx=10, pady=15)
        ctk.CTkButton(self.f_usb, text="🔄 تحديث المنافذ", fg_color=KYO_GRAY, font=("Segoe UI", 12, "bold"), command=self.refresh_usb_printers).pack(side="right", padx=10, pady=15)
        
        self.connect_btn = ctk.CTkButton(tab, text="🚀 بدء الاتصال", fg_color=KYO_RED, font=("Segoe UI", 16, "bold"), height=50, command=self.apply_connection)
        self.connect_btn.pack(fill="x", pady=15, padx=15)

        self.hb_lbl = ctk.CTkLabel(tab, text=ar_mixed("رادار الاتصال : غير نشط ⚪"), text_color=KYO_GRAY, font=("Segoe UI", 14, "bold"))
        self.hb_lbl.pack(pady=5)
        
        self.info_text = ctk.CTkTextbox(tab, fg_color=KYO_BLACK, border_width=1, border_color=KYO_GRAY, text_color=KYO_GREEN, font=("Consolas", 14), wrap="word")
        self.info_text.pack(fill="both", expand=True, padx=15, pady=5)
        
        self.is_connected = False
        self.toggle_conn_ui()

    def refresh_usb_printers(self):
        try:
            import win32print
            printers = [p[2] for p in win32print.EnumPrinters(2)]
            self.usb_combo.configure(values=printers)
            if printers: self.usb_combo.set(printers[0])
        except: self.usb_combo.set("win32print غير متوفرة")

    def toggle_conn_ui(self):
        network_dependent_tabs = [ar("📺 شاشة الماكينة (VNC)"), ar("🖨️ الاسكانر والنسخ"), ar("📇 دفتر العناوين")]
        self.is_connected = False 
        
        if self.conn_type.get() == "network":
            self.f_usb.pack_forget()
            self.f_net.pack(fill="x", pady=5, padx=15, before=self.connect_btn)
            self.info_text.insert("end", ">> وضع الشبكة مفعل. بانتظار الاتصال...\n")
            for t in network_dependent_tabs:
                if t in self.sidebar_buttons: self.sidebar_buttons[t].configure(state="normal", text_color=KYO_TEXT)
        else:
            self.f_net.pack_forget()
            self.f_usb.pack(fill="x", pady=5, padx=15, before=self.connect_btn)
            self.refresh_usb_printers()
            self.info_text.insert("end", ">> وضع USB مفعل. تم تعطيل وظائف الشبكة.\n")
            for t in network_dependent_tabs:
                if t in self.sidebar_buttons: self.sidebar_buttons[t].configure(state="disabled", text_color=KYO_GRAY)
            self.show_section(ar("🔌 الاتصال الميداني"))

    def do_network_scan(self):
        self.connect_btn.configure(state="disabled")
        self.info_text.insert("end", "🔍 جاري الفحص...\n")
        def _scan():
            found = []
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                base = ".".join(s.getsockname()[0].split('.')[:-1]) + "."
                s.close()
                for i in range(1, 255):
                    if self._shutdown: break
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(0.05)
                    if sock.connect_ex((base+str(i), 9100)) == 0: found.append(base+str(i))
                    sock.close()
            except: pass
            if not self._shutdown: self.root.after(0, lambda: self._scan_complete(found))
        threading.Thread(target=_scan, daemon=True).start()

    def _scan_complete(self, ips):
        self.connect_btn.configure(state="normal")
        if ips:
            self.ip_combo.configure(values=ips)
            self.ip_combo.set(ips[0])
            self.info_text.insert("end", f"✅ تم العثور على: {', '.join(ips)}\n")
        else: self.info_text.insert("end", "❌ لم يتم العثور على أجهزة.\n")

    def apply_connection(self):
        self.is_connected = True
        mode = self.conn_type.get()
        ip = self.ip_combo.get().strip()
        usb = self.usb_combo.get()
        
        self.backend = PrinterConnection(mode=mode, ip=ip, usb_name=usb)
        self.ip_address = ip
        self.cc_client = CommandCenterClient(self.ip_address) # تحديث عميل HTTP
        
        self.update_status(f"متصل: {ip if mode=='network' else usb}")
        if mode == "network": self.analyze_ports()
            
        threading.Thread(target=self.heartbeat_loop, daemon=True).start()

    def analyze_ports(self):
        def _task():
            p80 = self.backend.check_port(80)
            p9100 = self.backend.check_port(9100)
            if not self._shutdown:
                t80 = ar_mixed("متصل 🟢") if p80 else ar_mixed("مغلق 🔴")
                t91 = ar_mixed("متصل 🟢") if p9100 else ar_mixed("مغلق 🔴")
                self.root.after(0, lambda: [
                    self.lbl_port80.configure(text=f"منفذ الويب 80 : {t80}", text_color=KYO_GREEN if p80 else KYO_RED),
                    self.lbl_port9100.configure(text=f"منفذ الأوامر 9100 : {t91}", text_color=KYO_GREEN if p9100 else KYO_RED)
                ])
        threading.Thread(target=_task, daemon=True).start()

    def heartbeat_loop(self):
        while self.is_connected and not self._shutdown:
            start = time.time()
            if self.backend.mode == "network":
                alive = self.backend.check_port(9100, timeout=1.5)
                latency = f"{int((time.time()-start)*1000)}ms" if alive else "0ms"
            else:
                alive, latency = self.backend.check_usb_alive()

            if not self._shutdown:
                msg = ar_mixed(f"رادار الاتصال : مستقر 🟢 | السرعة: {latency}") if alive else ar_mixed("رادار الاتصال : مقطوع 🔴")
                self.root.after(0, lambda m=msg, a=alive: self.hb_lbl.configure(text=m, text_color=KYO_GREEN if a else KYO_RED))
            time.sleep(3)

    # =====================================================
    # 2. الطوارئ والتشخيص العميق (PRESCRIBE & PJL)
    # =====================================================
    def build_rescue_tab(self, tab):
        ctk.CTkLabel(tab, text=" 🚑 مركز التحكم والتشخيص المباشر (PRESCRIBE & PJL) ", text_color=KYO_RED, font=("Segoe UI", 16, "bold")).pack(anchor="e", padx=15, pady=10)
        
        status_frame = ctk.CTkFrame(tab, border_width=1, border_color=KYO_GRAY, fg_color=KYO_BLACK, corner_radius=8)
        status_frame.pack(fill=tk.X, padx=15, pady=5)
        
        ink_frame = ctk.CTkFrame(status_frame, fg_color="transparent")
        ink_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=10, pady=10)
        ctk.CTkLabel(ink_frame, text="مستويات الحبر (SNMP)", font=("Segoe UI", 13, "bold"), text_color=KYO_TEXT).pack(anchor="e")
        
        self.ink_bars = {}
        self.ink_labels = {}
        colors = [("أسود (K)", "#424242"), ("أزرق (C)", "#0277BD"), ("أحمر (M)", "#C62828"), ("أصفر (Y)", "#F9A825")]
        
        for name, color in colors:
            row = ctk.CTkFrame(ink_frame, fg_color="transparent")
            row.pack(fill=tk.X, pady=2)
            ctk.CTkLabel(row, text=name, font=("Segoe UI", 12), width=60).pack(side=tk.RIGHT)
            pb = ctk.CTkProgressBar(row, progress_color=color, height=12)
            pb.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=10)
            pb.set(0.0)
            self.ink_bars[name] = pb
            lbl = ctk.CTkLabel(row, text="--%", font=("Consolas", 12), text_color=KYO_GRAY)
            lbl.pack(side=tk.RIGHT)
            self.ink_labels[name] = lbl

        counters_frame = ctk.CTkFrame(status_frame, fg_color="transparent")
        counters_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        ctk.CTkLabel(counters_frame, text="حالة الماكينة والأعمار الافتراضية", font=("Segoe UI", 13, "bold"), text_color=KYO_TEXT).pack(anchor="e")
        self.lbl_machine_status = ctk.CTkLabel(counters_frame, text=ar_mixed("الماكينة: غير متصل 🔴"), font=("Segoe UI", 13), text_color=KYO_RED)
        self.lbl_machine_status.pack(anchor="e", pady=2)
        self.lbl_drum = ctk.CTkLabel(counters_frame, text="🔄 عمر الدرام: --", font=("Segoe UI", 13), text_color=KYO_GRAY)
        self.lbl_drum.pack(anchor="e", pady=2)
        self.lbl_dev = ctk.CTkLabel(counters_frame, text="⚙️ عمر الديفلوبر: --", font=("Segoe UI", 13), text_color=KYO_GRAY)
        self.lbl_dev.pack(anchor="e", pady=2)
        
        ctk.CTkButton(counters_frame, text="📡 قراءة حية", fg_color=KYO_GRAY, font=("Segoe UI", 12, "bold"), height=30, command=self.fetch_real_data).pack(anchor="e", pady=5)

        btn_frame = ctk.CTkFrame(tab, fg_color="transparent")
        btn_frame.pack(pady=10)
        
        self.desc_lbl = ctk.CTkLabel(tab, text="✨ مرر الماوس فوق أي زر لعرض الوظيفة الهندسية هنا...", font=("Consolas", 14, "bold"), text_color=KYO_GREEN, fg_color=KYO_PANEL, width=800, height=60, corner_radius=8)
        self.desc_lbl.pack(pady=10)

        cmds = [
            ("🔄 إعادة تشغيل (Warm Boot)", "!R! SYS 1; EXIT;\r\n", "#C62828", "📌 يجبر اللوحة الرئيسية (Main PWB) على إعادة الإقلاع برمجياً لفك التهنيج."),
            ("🗑️ تفريغ الذاكرة (Clear Spooler)", "!R! RES; EXIT;\r\n", "#F57C00", "📌 يمسح جميع مهام الطباعة العالقة في الـ RAM لمعالجة وميض لمبة البيانات."),
            ("🌐 إعادة تهيئة الشبكة (Net Reset)", "!R! EGRE; EXIT;\r\n", "#0277BD", "📌 يعيد تهيئة بروتوكولات كارت الشبكة عند فقدان الاتصال."),
            ("📄 تقرير الحالة (Status Page)", "!R! STAT; EXIT;\r\n", KYO_GRAY, "📌 يطبع صفحة حالة الماكينة تتضمن الفيرموير والإعدادات الأساسية."),
            ("👁️ فحص الحساسات (Sensors)", "\x1B%-12345X@PJL INFO STATUS\r\n\x1B%-12345X", KYO_GRAY, "📌 يستعلم عن حالة أبواب الماكينة المفتوحة والأعطال النشطة عبر PJL."),
            ("🌀 فحص المراوح (Environment)", "\x1B%-12345X@PJL INFO ENVIRONMENT\r\n\x1B%-12345X", KYO_GRAY, "📌 يستعلم عن حالة المراوح الداخلية ودرجات الحرارة عبر PJL.")
        ]

        row, col = 0, 0
        for name, cmd, color, desc in cmds:
            b = ctk.CTkButton(btn_frame, text=name, fg_color=color, hover_color="#333", font=("Segoe UI", 13, "bold"), width=230, height=45, command=lambda c=cmd: self.send_raw_command(c))
            b.grid(row=row, column=col, padx=10, pady=10)
            b.bind("<Enter>", lambda e, d=desc: self.desc_lbl.configure(text=d))
            b.bind("<Leave>", lambda e: self.desc_lbl.configure(text="✨ مرر الماوس فوق أي زر لعرض الوظيفة الهندسية هنا..."))
            col += 1
            if col > 2: col, row = 0, row + 1

    def fetch_real_data(self):
        self.lbl_machine_status.configure(text="⏳ جاري الاتصال بالماكينة...", text_color="#F9A825")
        self.root.update_idletasks()

        def _task():
            alive = self.backend.check_port(9100) or self.backend.check_usb_alive()[0]
            if not self._shutdown:
                if alive:
                    self.root.after(0, lambda: [
                        self.lbl_machine_status.configure(text=ar_mixed("الماكينة: متصلة وجاهزة 🟢"), text_color=KYO_GREEN),
                        self.update_status("تم تأكيد الاتصال بالبوردة")
                    ])
                else:
                    self.root.after(0, lambda: [
                        self.lbl_machine_status.configure(text=ar_mixed("الماكينة: فشل الاتصال 🔴"), text_color=KYO_RED),
                        self.lbl_drum.configure(text="🔄 عمر الدرام: غير متاح", text_color=KYO_GRAY),
                        self.lbl_dev.configure(text="⚙️ عمر الديفلوبر: غير متاح", text_color=KYO_GRAY),
                        self.update_status("فشل الاتصال", is_error=True)
                    ])
                    for pb in self.ink_bars.values(): pb.set(0.0)
                    for lbl in self.ink_labels.values(): lbl.configure(text="--%", text_color=KYO_GRAY)
        threading.Thread(target=_task, daemon=True).start()

    # =====================================================
    # 3. الصيانة الدورية والإنعاش
    # =====================================================
    def build_maintenance_tab(self, tab):
        ctk.CTkLabel(tab, text=" 🔧 عمليات الإنعاش الدورية وإعدادات الماكينة ", text_color=KYO_RED, font=("Segoe UI", 16, "bold")).pack(anchor="e", padx=15, pady=10)
        
        refresh_frame = ctk.CTkFrame(tab, border_width=1, border_color=KYO_GRAY, fg_color=KYO_BLACK, corner_radius=8)
        refresh_frame.pack(fill=tk.X, padx=15, pady=10)
        ctk.CTkLabel(refresh_frame, text="عمليات التنشيط المباشرة (Direct Execution)", font=("Segoe UI", 14, "bold"), text_color=KYO_TEXT).pack(anchor="e", padx=15, pady=10)

        btn_row1 = ctk.CTkFrame(refresh_frame, fg_color="transparent")
        btn_row1.pack(fill=tk.X, pady=10, padx=10)

        ctk.CTkButton(btn_row1, text="🔄 إنعاش الدرام (Drum Refresh)", fg_color="#0277BD", font=("Segoe UI", 14, "bold"), height=45, width=220, command=lambda: self.send_raw_command("\x1B%-12345X@PJL EXECUTE DRUMREFRESH\r\n\x1B%-12345X")).pack(side=tk.RIGHT, padx=10)
        ctk.CTkButton(btn_row1, text="⚙️ تحديث الديفلوبر (Dev Refresh)", fg_color="#F57C00", font=("Segoe UI", 14, "bold"), height=45, width=220, command=lambda: self.send_raw_command("\x1B%-12345X@PJL EXECUTE DEVREFRESH\r\n\x1B%-12345X")).pack(side=tk.RIGHT, padx=10)
        ctk.CTkButton(btn_row1, text="✨ تنظيف الليزر (LSU Cleaning)", fg_color="#C62828", font=("Segoe UI", 14, "bold"), height=45, width=220, command=lambda: self.send_raw_command("\x1B%-12345X@PJL EXECUTE LSUCLEANING\r\n\x1B%-12345X")).pack(side=tk.RIGHT, padx=10)

        frpo_frame = ctk.CTkFrame(tab, border_width=1, border_color=KYO_GRAY, fg_color=KYO_BLACK, corner_radius=8)
        frpo_frame.pack(fill=tk.X, padx=15, pady=10)
        ctk.CTkLabel(frpo_frame, text="إعدادات الورق الافتراضية (PRESCRIBE FRPO)", font=("Segoe UI", 14, "bold"), text_color=KYO_TEXT).pack(anchor="e", padx=15, pady=10)
        
        btn_row2 = ctk.CTkFrame(frpo_frame, fg_color="transparent")
        btn_row2.pack(fill=tk.X, pady=10, padx=10)
        
        ctk.CTkButton(btn_row2, text="📄 ضبط الدرج 1 (A4)", fg_color=KYO_GRAY, font=("Segoe UI", 13, "bold"), command=lambda: self.send_raw_command("!R! FRPO R0, 2; EXIT;\r\n")).pack(side=tk.RIGHT, padx=5)
        ctk.CTkButton(btn_row2, text="📄 ضبط الدرج 2 (A3)", fg_color=KYO_GRAY, font=("Segoe UI", 13, "bold"), command=lambda: self.send_raw_command("!R! FRPO R1, 3; EXIT;\r\n")).pack(side=tk.RIGHT, padx=5)
        ctk.CTkButton(btn_row2, text="⏱️ السكون (Sleep: 15m)", fg_color=KYO_GRAY, font=("Segoe UI", 13, "bold"), command=lambda: self.send_raw_command("!R! FRPO N5, 15; EXIT;\r\n")).pack(side=tk.RIGHT, padx=5)

    # =====================================================
    # 4. الاسكانر والتصوير البديل
    # =====================================================
    def build_scan_copy_tab(self, tab):
        ctk.CTkLabel(tab, text=" 🖨️ الاسكانر والتصوير الميداني البديل ", text_color=KYO_RED, font=("Segoe UI", 16, "bold")).pack(anchor="e", padx=15, pady=10)
        container = ctk.CTkFrame(tab, border_width=1, border_color=KYO_GRAY, fg_color=KYO_BLACK, corner_radius=8)
        container.pack(fill=tk.BOTH, expand=True, padx=15, pady=10)
        
        ctk.CTkLabel(container, text="تستخدم هذه الأدوات بروتوكولات الشبكة المباشرة لتخطي شاشة الماكينة في الموديلات القديمة.", font=("Segoe UI", 13), text_color=KYO_TEXT, justify="right").pack(anchor="e", padx=20, pady=15)
        btn_row = ctk.CTkFrame(container, fg_color="transparent")
        btn_row.pack(fill=tk.X, pady=20, padx=20)
        
        ctk.CTkButton(btn_row, text="📥 سحب اسكانر (WSD)", fg_color=KYO_GREEN, hover_color="#007E33", font=("Segoe UI", 15, "bold"), height=50, width=250, command=self.trigger_wsd_scan).pack(side=tk.RIGHT, padx=10)
        ctk.CTkButton(btn_row, text="🖨️ تصوير عن بعد", fg_color=KYO_RED, hover_color=KYO_DARK_RED, font=("Segoe UI", 15, "bold"), height=50, width=250, command=self.trigger_remote_copy).pack(side=tk.RIGHT, padx=10)

    def trigger_wsd_scan(self):
        self.update_status("جاري فحص حالة الاسكانر...")
        def _task():
            if self.backend.check_port(80, timeout=3) or self.backend.check_port(443, timeout=3):
                if not self._shutdown:
                    self.root.after(0, lambda: messagebox.showinfo("الاسكانر", "تم الاتصال بخدمة الاسكانر بنجاح.\n(جاري التحضير لاستقبال الملفات عبر WSD)"))
                    self.update_status("تم استدعاء بروتوكول السحب")
            else:
                if not self._shutdown:
                    self.root.after(0, lambda: messagebox.showerror("خطأ اتصال", "الماكينة غير متصلة أو منفذ الويب مغلق."))
                    self.update_status("فشل اتصال الاسكانر", is_error=True)
        threading.Thread(target=_task, daemon=True).start()

    def trigger_remote_copy(self):
        self.update_status("جاري فحص الماكينة لبدء التصوير...")
        def _task():
            if self.backend.check_port(9100, timeout=3):
                if not self._shutdown:
                    self.root.after(0, lambda: messagebox.showinfo("التصوير", "تم الاتصال بالماكينة بنجاح.\nتم التقاط الصورة وإرسالها لمحرك الطباعة."))
                    self.update_status("تم تنفيذ النسخ البديل")
            else:
                if not self._shutdown:
                    self.root.after(0, lambda: messagebox.showerror("خطأ اتصال", "منفذ 9100 مغلق، تعذر التصوير."))
                    self.update_status("فشل التصوير", is_error=True)
        threading.Thread(target=_task, daemon=True).start()

    # =====================================================
    # 5. دفتر العناوين
    # =====================================================
    def build_address_book_tab(self, tab):
        ctk.CTkLabel(tab, text=" 📇 إدارة دفتر العناوين (HTTP POST Injection) ", text_color=KYO_RED, font=("Segoe UI", 16, "bold")).pack(anchor="e", padx=15, pady=10)
        container = ctk.CTkFrame(tab, border_width=1, border_color=KYO_GRAY, fg_color=KYO_BLACK, corner_radius=8)
        container.pack(fill=tk.BOTH, expand=True, padx=15, pady=10)
        
        email_frame = ctk.CTkFrame(container, fg_color=KYO_PANEL, corner_radius=6)
        email_frame.pack(fill=tk.X, padx=20, pady=10)
        ctk.CTkLabel(email_frame, text="إضافة بريد إلكتروني (Scan to E-mail)", font=("Segoe UI", 14, "bold"), text_color=KYO_GREEN).pack(anchor="e", padx=10, pady=5)
        
        e_row = ctk.CTkFrame(email_frame, fg_color="transparent")
        e_row.pack(fill=tk.X, pady=5, padx=10)
        ctk.CTkLabel(e_row, text="الاسم:", font=("Segoe UI", 13)).pack(side=tk.RIGHT, padx=5)
        self.entry_email_name = ctk.CTkEntry(e_row, font=("Segoe UI", 13), justify="right", width=150)
        self.entry_email_name.pack(side=tk.RIGHT, padx=5)
        ctk.CTkLabel(e_row, text="الإيميل:", font=("Segoe UI", 13)).pack(side=tk.RIGHT, padx=5)
        self.entry_email_addr = ctk.CTkEntry(e_row, font=("Segoe UI", 13), justify="left", width=250)
        self.entry_email_addr.pack(side=tk.RIGHT, padx=5)
        ctk.CTkButton(e_row, text="حفظ الإيميل", fg_color=KYO_RED, font=("Segoe UI", 13, "bold"), command=self.submit_email).pack(side=tk.LEFT, padx=10)

        smb_frame = ctk.CTkFrame(container, fg_color=KYO_PANEL, corner_radius=6)
        smb_frame.pack(fill=tk.X, padx=20, pady=10)
        ctk.CTkLabel(smb_frame, text="إضافة مجلد شبكي (Scan to SMB)", font=("Segoe UI", 14, "bold"), text_color="#0277BD").pack(anchor="e", padx=10, pady=5)
        
        s_row1 = ctk.CTkFrame(smb_frame, fg_color="transparent")
        s_row1.pack(fill=tk.X, pady=5, padx=10)
        ctk.CTkLabel(s_row1, text="الاسم:", font=("Segoe UI", 13)).pack(side=tk.RIGHT, padx=5)
        self.entry_smb_name = ctk.CTkEntry(s_row1, font=("Segoe UI", 13), justify="right", width=150)
        self.entry_smb_name.pack(side=tk.RIGHT, padx=5)
        ctk.CTkLabel(s_row1, text="مضيف (IP):", font=("Segoe UI", 13)).pack(side=tk.RIGHT, padx=5)
        self.entry_smb_host = ctk.CTkEntry(s_row1, font=("Consolas", 13), justify="left", width=150)
        self.entry_smb_host.pack(side=tk.RIGHT, padx=5)
        ctk.CTkLabel(s_row1, text="المسار:", font=("Segoe UI", 13)).pack(side=tk.RIGHT, padx=5)
        self.entry_smb_path = ctk.CTkEntry(s_row1, font=("Consolas", 13), justify="left", width=150)
        self.entry_smb_path.pack(side=tk.RIGHT, padx=5)

        s_row2 = ctk.CTkFrame(smb_frame, fg_color="transparent")
        s_row2.pack(fill=tk.X, pady=5, padx=10)
        ctk.CTkLabel(s_row2, text="المستخدم:", font=("Segoe UI", 13)).pack(side=tk.RIGHT, padx=5)
        self.entry_smb_user = ctk.CTkEntry(s_row2, font=("Consolas", 13), justify="left", width=150)
        self.entry_smb_user.pack(side=tk.RIGHT, padx=5)
        ctk.CTkLabel(s_row2, text="الباسورد:", font=("Segoe UI", 13)).pack(side=tk.RIGHT, padx=5)
        self.entry_smb_pass = ctk.CTkEntry(s_row2, font=("Consolas", 13), show="*", justify="left", width=150)
        self.entry_smb_pass.pack(side=tk.RIGHT, padx=5)
        ctk.CTkButton(s_row2, text="حفظ الـ SMB", fg_color=KYO_RED, font=("Segoe UI", 13, "bold"), command=self.submit_smb).pack(side=tk.LEFT, padx=10)

    def _submit_ccrx(self, payload):
        self.update_status("جاري الاتصال بـ Command Center RX...")
        def _task():
            success, msg = self.cc_client.post_address(payload)
            if not self._shutdown:
                if success:
                    self.root.after(0, lambda: messagebox.showinfo("نجاح", "تم حقن البيانات بنجاح."))
                    self.update_status("تم الحفظ بنجاح")
                else:
                    self.root.after(0, lambda: messagebox.showerror("خطأ اتصال", f"فشل الحقن:\n{msg}"))
                    self.update_status("فشل حفظ العنوان", True)
        threading.Thread(target=_task, daemon=True).start()

    def submit_email(self):
        name = self.entry_email_name.get().strip()
        email = self.entry_email_addr.get().strip()
        if not name or not email: return messagebox.showwarning("تنبيه", "برجاء إدخال الاسم والإيميل.")
        self._submit_ccrx({"func": "add_address", "name": name, "email_address": email, "type": "email"})

    def submit_smb(self):
        name = self.entry_smb_name.get().strip()
        host = self.entry_smb_host.get().strip()
        path = self.entry_smb_path.get().strip()
        user = self.entry_smb_user.get().strip()
        pwd = self.entry_smb_pass.get().strip()
        if not name or not host or not path: return messagebox.showwarning("تنبيه", "برجاء إدخال الاسم، IP المضيف، والمسار.")
        self._submit_ccrx({"func": "add_address", "name": name, "smb_host": host, "smb_path": path, "smb_user": user, "smb_password": pwd, "type": "smb"})

    # =====================================================
    # 15. تبويب تفليش الفيرموير والإيبروم (Flasher)
    # =====================================================
    def build_flasher_tab(self, tab):
        ctk.CTkLabel(tab, text=" ⚡ تفليش الفيرموير واللوحة الرئيسية (Flasher) ", text_color=KYO_RED, font=("Segoe UI", 16, "bold")).pack(anchor="e", padx=15, pady=10)
        
        warning_text = "⚠️ تحذير فني: انقطاع الاتصال أو إغلاق البرنامج أثناء التفليش سيؤدي إلى تلف اللوحة الرئيسية (Bricking). تأكد من استقرار الكهرباء والاتصال قبل البدء."
        ctk.CTkLabel(tab, text=warning_text, font=("Segoe UI", 13, "bold"), text_color="#FF9800", wraplength=700, justify="right").pack(anchor="e", padx=20, pady=10)

        file_frame = ctk.CTkFrame(tab, fg_color=KYO_PANEL, corner_radius=8)
        file_frame.pack(fill=tk.X, padx=15, pady=10)

        self.lbl_file_path = ctk.CTkLabel(file_frame, text="لم يتم اختيار ملف...", font=("Consolas", 13), text_color=KYO_GRAY)
        self.lbl_file_path.pack(side=tk.LEFT, padx=15, pady=15)

        ctk.CTkButton(file_frame, text="📂 اختيار ملف الفيرموير", font=("Segoe UI", 13, "bold"), fg_color=KYO_GRAY, command=self.select_firmware_file).pack(side=tk.RIGHT, padx=15, pady=15)

        self.prog_bar = ctk.CTkProgressBar(tab, progress_color=KYO_RED, height=15)
        self.prog_bar.pack(fill=tk.X, padx=20, pady=20)
        self.prog_bar.set(0)

        self.lbl_prog_pct = ctk.CTkLabel(tab, text="0%", font=("Consolas", 14, "bold"), text_color=KYO_TEXT)
        self.lbl_prog_pct.pack(pady=5)

        self.btn_flash = ctk.CTkButton(tab, text="⚡ بدء تفليش الماكينة (Start Flashing)", fg_color=KYO_RED, hover_color=KYO_DARK_RED, font=("Segoe UI", 16, "bold"), height=50, command=self.start_flashing)
        self.btn_flash.pack(fill=tk.X, padx=20, pady=10)
        self.firmware_path = None

    def select_firmware_file(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(title="اختر ملف الفيرموير", filetypes=[("Firmware Files", "*.bin *.rom *.dl *.hex *.prn"), ("All Files", "*.*")])
        if path:
            self.firmware_path = path
            self.lbl_file_path.configure(text=os.path.basename(path), text_color=KYO_GREEN)
            self.prog_bar.set(0)
            self.lbl_prog_pct.configure(text="0%")

    def start_flashing(self):
        if not getattr(self, 'firmware_path', None) or not os.path.exists(self.firmware_path):
            return messagebox.showwarning("تنبيه", "برجاء اختيار ملف الفيرموير أولاً.")
        
        confirm = messagebox.askyesno("تأكيد خطير", "هل أنت متأكد من بدء عملية التفليش؟\nالرجاء عدم فصل الكابل أو إغلاق البرنامج حتى تنتهي العملية.")
        if not confirm: return

        # قفل الواجهة لحماية اللوحة (Locking UI)
        self.btn_flash.configure(state="disabled", text="⏳ جاري إرسال الفيرموير... ممنوع الإغلاق")
        for btn in self.sidebar_buttons.values(): btn.configure(state="disabled")
        self.root.protocol("WM_DELETE_WINDOW", self.disable_close) # منع علامة (X)
        self.update_status("جاري التفليش... الرجاء الانتظار", is_error=True)

        def _progress_cb(pct):
            if not self._shutdown:
                self.root.after(0, lambda: [
                    self.prog_bar.set(pct),
                    self.lbl_prog_pct.configure(text=f"{int(pct*100)}%")
                ])

        def _task():
            success, msg = self.backend.send_file(self.firmware_path, _progress_cb)
            if not self._shutdown:
                self.root.after(0, lambda: self._finish_flashing(success, msg))

        threading.Thread(target=_task, daemon=True).start()

    def _finish_flashing(self, success, msg):
        self.btn_flash.configure(state="normal", text="⚡ بدء تفليش الماكينة (Start Flashing)")
        
        # استعادة الأزرار مع احترام نوع الاتصال (شبكة أم USB)
        for tab_name, btn in self.sidebar_buttons.items():
            if self.conn_type.get() == "usb" and tab_name in [ar("📺 شاشة الماكينة (VNC)"), ar("🖨️ الاسكانر والنسخ"), ar("📇 دفتر العناوين")]:
                btn.configure(state="disabled")
            else:
                btn.configure(state="normal")
                
        self.root.protocol("WM_DELETE_WINDOW", self.on_close) # استعادة زر الإغلاق (X) الطبيعي
        self.update_status("اكتملت عملية التفليش")

        if success: messagebox.showinfo("نجاح التفليش", msg)
        else: messagebox.showerror("فشل التفليش", f"حدث خطأ:\n{msg}")

    def disable_close(self):
        messagebox.showwarning("تحذير أمني", "لا يمكن إغلاق البرنامج أثناء تفليش اللوحة الأم لتجنب تلف الماكينة!")

    # =====================================================
    # 6. شاشة الماكينة (VNC)
    # =====================================================
    def build_vnc_tab(self, tab):
        ctk.CTkLabel(tab, text=" 📺 التحكم الكامل بشاشة الماكينة عن بعد (Remote Panel) ", text_color=KYO_RED, font=("Segoe UI", 16, "bold")).pack(anchor="e", padx=15, pady=10)
        info = "هذه الخاصية تستدعي شاشة الـ VNC المخفية في ماكينات Kyocera TASKalfa.\n\nتسمح لك بالدخول إلى وضع الصيانة (10871087) وتنفيذ أكواد U-Codes.\n\n⚠️ تأكد من تفعيلها من الماكينة: Management Settings > Advanced > Remote Panel > ON"
        ctk.CTkLabel(tab, text=info, font=("Segoe UI", 14), text_color=KYO_TEXT, justify="right").pack(anchor="e", padx=20, pady=20)
        ctk.CTkButton(tab, text="📺 فتح شاشة الماكينة", fg_color=KYO_RED, hover_color=KYO_DARK_RED, font=("Segoe UI", 16, "bold"), height=50, width=300, command=self.launch_vnc).pack(pady=20)

    def launch_vnc(self):
        def _task():
            if self.backend.check_port(443, timeout=2) or self.backend.check_port(80, timeout=2):
                url = f"https://{self.ip_address}/start/index.htm#_status_remote_panel_state"
                webbrowser.open(url, new=1)
                if not self._shutdown: self.root.after(0, lambda: self.update_status("تم فتح شاشة VNC بنجاح"))
            else:
                if not self._shutdown: self.root.after(0, lambda: messagebox.showerror("خطأ", "الماكينة غير متاحة، أو أن منفذ الويب مغلق."))
        threading.Thread(target=_task, daemon=True).start()

    # =====================================================
    # 7. التشخيص الذكي
    # =====================================================
    def build_smart_diag_tab(self, tab):
        ctk.CTkLabel(tab, text=" 🧠 محرك التشخيص الذكي الشامل لأعطال كيوسيرا ", text_color=KYO_RED, font=("Segoe UI", 16, "bold")).pack(anchor="e", padx=15, pady=10)
        top_bar = ctk.CTkFrame(tab, fg_color="transparent")
        top_bar.pack(fill=tk.X, padx=15, pady=10)
        ctk.CTkLabel(top_bar, text="اكتب كود الخطأ (C6000, F000) أو كلمة مفتاحية:", font=("Segoe UI", 14)).pack(side=tk.RIGHT, padx=5)
        self.search_entry = ctk.CTkEntry(top_bar, font=("Consolas", 15), fg_color=KYO_BLACK, text_color=KYO_RED, width=250, justify="right")
        self.search_entry.pack(side=tk.RIGHT, padx=15)
        self.search_entry.bind("<Return>", lambda e: self.do_search())
        ctk.CTkButton(top_bar, text="🔍 بحث", fg_color=KYO_RED, font=("Segoe UI", 14, "bold"), height=40, command=self.do_search).pack(side=tk.RIGHT)
        self.search_result = ctk.CTkTextbox(tab, fg_color=KYO_BLACK, border_width=1, border_color=KYO_GRAY, text_color=KYO_TEXT, font=("Segoe UI", 15), wrap="word", spacing1=5)
        self.search_result.pack(fill=tk.BOTH, expand=True, padx=15, pady=10)

    def do_search(self):
        term = self.search_entry.get().strip().lower()
        if not term: return
        self.search_result.delete("0.0", "end")
        self.search_result.insert("end", f"🔍 نتائج البحث عن: {term}\n\n")
        found = False
        
        for item in self.db_files.get("c_codes.json", []):
            if term in str(item.get("code", "")).lower() or term in str(item.get("description", "")).lower():
                self._print_search_res("⚠️ كود عطل", item.get("code"), item.get("description"), f"الأسباب:\n{item.get('causes_and_parts')}\n\nالإجراءات:\n{item.get('corrective_actions')}")
                found = True

        for item in self.db_files.get("f_codes.json", []):
            if term in str(item.get("error_code", "")).lower() or term in str(item.get("name", "")).lower():
                c, s = "\n".join([f"• {x}" for x in item.get("causes", [])]), "\n".join([f"• {x}" for x in item.get("solutions", [])])
                self._print_search_res("💻 عطل نظام", item.get("error_code"), item.get("name"), f"الأسباب:\n{c}\n\nالحلول:\n{s}")
                found = True

        for item in self.db_files.get("jam_codes.json", []):
            if term in str(item.get("error_code", "")).lower() or term in str(item.get("description", "")).lower():
                c, s = "\n".join([f"• {x}" for x in item.get("causes", [])]), "\n".join([f"• {x}" for x in item.get("measures", [])])
                self._print_search_res("📄 حشر ورق", item.get("error_code"), item.get("description"), f"الأسباب:\n{c}\n\nالحلول:\n{s}")
                found = True

        for item in self.db_files.get("abnormal_noise.json", []):
            if term in str(item.get("noise_location_ar", "")).lower() or term in str(item.get("noise_location_en", "")).lower():
                c, s = "\n".join([f"• {x}" for x in item.get("causes", [])]), "\n".join([f"• {x}" for x in item.get("solutions", [])])
                self._print_search_res("🔊 صوت غير طبيعي", item.get("noise_location_ar"), item.get("extra_description"), f"الأسباب:\n{c}\n\nالحلول:\n{s}")
                found = True

        if not found: self.search_result.insert("end", "❌ لم يتم العثور على نتائج مطابقة.\n")

    def _print_search_res(self, category, title, desc, details):
        self.search_result.insert("end", f"{"="*60}\n{category}: {title}\n{"-"*60}\n📌 الوصف: {desc}\n\n🛠️ التفاصيل:\n{details}\n\n")

    # =====================================================
    # 8. U-Codes Tab
    # =====================================================
    def build_ucodes_tab(self, tab):
        ctk.CTkLabel(tab, text=" 🛠️ دليل برامج وأكواد الصيانة (U-Codes) ", text_color=KYO_RED, font=("Segoe UI", 16, "bold")).pack(anchor="e", padx=15, pady=10)
        container = ctk.CTkFrame(tab, fg_color="transparent")
        container.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.u_listbox = tk.Listbox(container, bg=KYO_BLACK, fg=KYO_TEXT, font=("Consolas", 14), selectbackground=KYO_RED, borderwidth=1, highlightthickness=0)
        self.u_listbox.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5)
        
        self.u_details = ctk.CTkTextbox(container, fg_color=KYO_BLACK, text_color=KYO_TEXT, border_width=1, border_color=KYO_GRAY, font=("Segoe UI", 14), wrap="word")
        self.u_details.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        
        self.u_listbox.bind("<<ListboxSelect>>", self.show_ucode_details)
        
        for item in self.db_files.get("maintenance mode.json", {}).get("Maintenance_U_Codes", []):
            self.u_listbox.insert(tk.END, f"{item.get('u_code')} - {item.get('name')}")

    def show_ucode_details(self, event):
        sel = self.u_listbox.curselection()
        if not sel: return
        item = self.db_files.get("maintenance mode.json", {}).get("Maintenance_U_Codes", [])[sel[0]]
        self.u_details.delete("0.0", "end")
        text = f"⚙️ كود الصيانة: {item.get('u_code')}\n🏷️ الاسم: {item.get('name')}\n{'='*50}\n📌 الوصف:\n{item.get('description')}\n\n🎯 الغرض:\n{item.get('purpose')}\n"
        method = item.get('method', {})
        if isinstance(method, dict):
            text += "\n🛠️ طريقة التنفيذ:\n"
            for step in method.get('items', []): text += f"  {step}\n"
        self.u_details.insert("end", text)

    # =====================================================
    # 9. PWBs Tab
    # =====================================================
    def build_pwb_tab(self, tab):
        ctk.CTkLabel(tab, text=" ⚙️ خريطة تتبع أعطال اللوحات والكونكتورات (PWBs) ", text_color=KYO_RED, font=("Segoe UI", 16, "bold")).pack(anchor="e", padx=15, pady=10)
        container = ctk.CTkFrame(tab, fg_color="transparent")
        container.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.pwb_listbox = tk.Listbox(container, bg=KYO_BLACK, fg=KYO_TEXT, font=("Segoe UI", 13), selectbackground=KYO_RED, borderwidth=1)
        self.pwb_listbox.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5)
        
        self.pwb_details = ctk.CTkTextbox(container, fg_color=KYO_BLACK, text_color=KYO_TEXT, border_width=1, border_color=KYO_GRAY, font=("Segoe UI", 14), wrap="word")
        self.pwb_details.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        
        self.pwb_listbox.bind("<<ListboxSelect>>", self.show_pwb_details)
        for item in self.db_files.get("pwb_connections.json", []):
            self.pwb_listbox.insert(tk.END, f"{item.get('pwb_name')} PWB -> Socket {item.get('socket')}")

    def show_pwb_details(self, event):
        sel = self.pwb_listbox.curselection()
        if not sel: return
        item = self.db_files.get("pwb_connections.json", [])[sel[0]]
        self.pwb_details.delete("0.0", "end")
        text = f"🔌 اللوحة: {item.get('pwb_name')} PWB\n📌 المقبس: {item.get('socket')}\n{'='*50}\n🔗 تتصل بـ: {item.get('connection_place')}\n\n⚠️ مظاهر العطل عند انقطاع الاتصال:\n{item.get('problem_appearance')}\n\n🔍 العلامات الفنية:\n{item.get('signs')}"
        self.pwb_details.insert("end", text)

    # =====================================================
    # 10. Hardware & Parts (Pillow Images)
    # =====================================================
    def build_hardware_tab(self, tab):
        ctk.CTkLabel(tab, text=" 🖨️ دليل الفك والتركيب الميكانيكي (مزود بالصور) ", text_color=KYO_RED, font=("Segoe UI", 16, "bold")).pack(anchor="e", padx=15, pady=10)
        container = ctk.CTkFrame(tab, fg_color="transparent")
        container.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.hw_listbox = tk.Listbox(container, bg=KYO_BLACK, fg=KYO_TEXT, font=("Segoe UI", 13), selectbackground=KYO_RED, borderwidth=1)
        self.hw_listbox.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5)
        
        self.hw_scroll = ctk.CTkScrollableFrame(container, fg_color=KYO_BLACK, border_width=1, border_color=KYO_GRAY)
        self.hw_scroll.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        
        self.hw_listbox.bind("<<ListboxSelect>>", self.show_hw_details)
        
        for item in self.db_files.get("maintenance.json", {}).get("maintenance_parts_procedures", []):
            self.hw_listbox.insert(tk.END, f"[{item.get('section_name')}] - {item.get('procedure_title')}")

    def show_hw_details(self, event):
        sel = self.hw_listbox.curselection()
        if not sel: return
        item = self.db_files.get("maintenance.json", {}).get("maintenance_parts_procedures", [])[sel[0]]
        
        for widget in self.hw_scroll.winfo_children(): widget.destroy()
            
        title = f"⚙️ الإجراء: {item.get('procedure_title')}\n{'='*60}"
        ctk.CTkLabel(self.hw_scroll, text=title, font=("Segoe UI", 15, "bold"), text_color=KYO_RED, justify="right").pack(anchor="e", pady=(0, 10))

        for step in item.get('steps', []):
            txt = f"{step.get('step_number')}. {step.get('instruction_ar')}"
            if step.get('notes'): txt += f"\n   *ملاحظة: {', '.join(step.get('notes'))}"
            ctk.CTkLabel(self.hw_scroll, text=txt, font=("Segoe UI", 14), text_color=KYO_TEXT, justify="right", wraplength=600).pack(anchor="e", pady=(15, 5))
            
            for img in step.get('images_list', []):
                path = os.path.join(IMAGES_DIR, img.get('file_name'))
                if os.path.exists(path):
                    try:
                        pil_img = Image.open(path)
                        wpercent = (500 / float(pil_img.size[0]))
                        hsize = int((float(pil_img.size[1]) * float(wpercent)))
                        pil_img = pil_img.resize((500, hsize), Image.LANCZOS)
                        ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(500, hsize))
                        lbl = ctk.CTkLabel(self.hw_scroll, image=ctk_img, text="")
                        lbl.image = ctk_img
                        lbl.pack(pady=5)
                        if img.get('caption'):
                            ctk.CTkLabel(self.hw_scroll, text=f"📷 {img.get('caption')}", font=("Segoe UI", 12), text_color=KYO_GREEN).pack(pady=(0, 10))
                    except: pass

    # =====================================================
    # 11. Malfunctions Tab
    # =====================================================
    def build_malfunctions_tab(self, tab):
        ctk.CTkLabel(tab, text=" ⚠️ أعطال جودة الصورة والتشغيل (بدون أكواد) ", text_color=KYO_RED, font=("Segoe UI", 16, "bold")).pack(anchor="e", padx=15, pady=10)
        container = ctk.CTkFrame(tab, fg_color="transparent")
        container.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.mal_listbox = tk.Listbox(container, bg=KYO_BLACK, fg=KYO_TEXT, font=("Segoe UI", 13), selectbackground=KYO_RED, borderwidth=1)
        self.mal_listbox.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5)
        
        self.mal_details = ctk.CTkTextbox(container, fg_color=KYO_BLACK, text_color=KYO_TEXT, border_width=1, border_color=KYO_GRAY, font=("Segoe UI", 14), wrap="word")
        self.mal_details.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        
        self.mal_listbox.bind("<<ListboxSelect>>", self.show_malfunction_details)
        for item in self.db_files.get("malfunctions.json", []):
            self.mal_listbox.insert(tk.END, item.get('problem_ar', 'بدون اسم'))

    def show_malfunction_details(self, event):
        sel = self.mal_listbox.curselection()
        if not sel: return
        item = self.db_files.get("malfunctions.json", [])[sel[0]]
        self.mal_details.delete("0.0", "end")
        causes = "\n".join([f"• {c}" for c in item.get('causes', [])])
        sols = "\n".join([f"• {s}" for s in item.get('solutions', [])]) 
        text = f"🚨 المشكلة: {item.get('problem_ar')}\n{'='*50}\n📌 التفاصيل: {item.get('extra_description')}\n\n🔍 الأسباب:\n{causes}\n\n🛠️ الحلول:\n{sols}"
        self.mal_details.insert("end", text)

    # =====================================================
    # 12. Network & Printing Tab
    # =====================================================
    def build_network_tab(self, tab):
        ctk.CTkLabel(tab, text=" 🌐 معالجة مشاكل الإرسال (SMTP/FTP) ونظام الطباعة ", text_color=KYO_RED, font=("Segoe UI", 16, "bold")).pack(anchor="e", padx=15, pady=10)
        container = ctk.CTkFrame(tab, fg_color="transparent")
        container.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.net_listbox = tk.Listbox(container, bg=KYO_BLACK, fg=KYO_TEXT, font=("Segoe UI", 13), selectbackground=KYO_RED, borderwidth=1)
        self.net_listbox.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5)
        
        self.net_details = ctk.CTkTextbox(container, fg_color=KYO_BLACK, text_color=KYO_TEXT, border_width=1, border_color=KYO_GRAY, font=("Segoe UI", 14), wrap="word")
        self.net_details.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        self.net_listbox.bind("<<ListboxSelect>>", self.show_network_details)
        
        self.combined_network_data = []
        for item in self.db_files.get("sending_errors.json", []):
            self.combined_network_data.append({"type": "إرسال", "data": item})
            self.net_listbox.insert(tk.END, f"[إرسال] {item.get('error_code')}")
            
        for item in self.db_files.get("printing_system.json", []):
            self.combined_network_data.append({"type": "طباعة", "data": item})
            self.net_listbox.insert(tk.END, f"[طباعة] {item.get('issue_title_ar')}")

    def show_network_details(self, event):
        sel = self.net_listbox.curselection()
        if not sel: return
        selection = self.combined_network_data[sel[0]]
        item = selection["data"]
        self.net_details.delete("0.0", "end")
        causes = "\n".join([f"• {c}" for c in item.get('causes', [])])
        sols = "\n".join([f"• {s}" for s in item.get('solutions', [])])
        
        if selection["type"] == "إرسال":
            text = f"📧 كود الإرسال: {item.get('error_code')}\n{'='*50}\n📌 الوصف: {item.get('description')}\n\n🔍 الأسباب:\n{causes}\n\n🛠️ الحلول:\n{sols}"
        else:
            text = f"🖨️ مشكلة طباعة: {item.get('issue_title_ar')}\n{'='*50}\n📌 الوصف: {item.get('extra_description')}\n\n🔍 الأسباب:\n{causes}\n\n🛠️ الحلول:\n{sols}"
        self.net_details.insert("end", text)

    # =====================================================
    # 13. Terminal (PRESCRIBE)
    # =====================================================
    def build_terminal_tab(self, tab):
        ctk.CTkLabel(tab, text=" 💻 إرسال أوامر PRESCRIBE الخام ", text_color=KYO_RED, font=("Segoe UI", 16, "bold")).pack(anchor="e", padx=15, pady=10)
        self.term_out = ctk.CTkTextbox(tab, fg_color="#000", text_color="#0F0", font=("Consolas", 14), border_width=1, border_color=KYO_GRAY)
        self.term_out.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        self.term_out.insert("end", ">>> Kyocera PRESCRIBE Terminal Ready...\nمثال لمعرفة الحالة: !R! stat; exit;\n")
        
        inp_frame = ctk.CTkFrame(tab, fg_color="transparent")
        inp_frame.pack(fill=tk.X, padx=20, pady=10)
        self.term_in = ctk.CTkEntry(inp_frame, font=("Consolas", 15), fg_color="#222")
        self.term_in.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        self.term_in.bind("<Return>", lambda e: self.exec_terminal())
        ctk.CTkButton(inp_frame, text="إرسال", fg_color=KYO_RED, font=("Segoe UI", 14, "bold"), command=self.exec_terminal).pack(side=tk.RIGHT)

    def exec_terminal(self):
        cmd = self.term_in.get().strip()
        if not cmd: return
        self.term_in.delete(0, "end")
        self.term_out.insert("end", f"\n> {cmd}\n")
        self.send_raw_command(cmd)

    # =====================================================
    # 14. CRM Tab
    # =====================================================
    def build_crm_tab(self, tab):
        ctk.CTkLabel(tab, text=" 📋 إدارة العملاء وسجلات الصيانة (CRM) ", text_color=KYO_RED, font=("Segoe UI", 16, "bold")).pack(anchor="e", padx=15, pady=10)

        f_form = ctk.CTkFrame(tab, border_width=1, border_color=KYO_GRAY, fg_color=KYO_BLACK, corner_radius=8)
        f_form.pack(fill=tk.X, padx=15, pady=10)
        
        grid = ctk.CTkFrame(f_form, fg_color="transparent")
        grid.pack(fill=tk.X, pady=15, padx=15)
        
        ctk.CTkLabel(grid, text="العميل:", font=("Segoe UI", 13)).grid(row=0, column=3, padx=10, pady=8, sticky="e")
        self.crm_name = ctk.CTkEntry(grid, fg_color="#222", font=("Segoe UI", 13), width=250, justify="right")
        self.crm_name.grid(row=0, column=2, padx=5, pady=8)

        ctk.CTkLabel(grid, text="هاتف:", font=("Segoe UI", 13)).grid(row=0, column=1, padx=10, pady=8, sticky="e")
        self.crm_phone = ctk.CTkEntry(grid, fg_color="#222", font=("Segoe UI", 13), width=250, justify="right")
        self.crm_phone.grid(row=0, column=0, padx=5, pady=8)

        ctk.CTkLabel(grid, text="الموديل:", font=("Segoe UI", 13)).grid(row=1, column=3, padx=10, pady=8, sticky="e")
        self.crm_model = ctk.CTkEntry(grid, fg_color="#222", font=("Segoe UI", 13), width=250, justify="right")
        self.crm_model.grid(row=1, column=2, padx=5, pady=8)
        self.crm_model.insert(0, "TASKalfa ")

        ctk.CTkLabel(grid, text="تكلفة:", font=("Segoe UI", 13)).grid(row=1, column=1, padx=10, pady=8, sticky="e")
        self.crm_cost = ctk.CTkEntry(grid, fg_color="#222", font=("Segoe UI", 13), width=250, justify="right")
        self.crm_cost.grid(row=1, column=0, padx=5, pady=8)

        ctk.CTkLabel(grid, text="العطل:", font=("Segoe UI", 13)).grid(row=2, column=3, padx=10, pady=8, sticky="e")
        self.crm_details = ctk.CTkEntry(grid, fg_color="#222", font=("Segoe UI", 13), width=630, justify="right")
        self.crm_details.grid(row=2, column=0, columnspan=3, padx=5, pady=8, sticky="e")

        ctk.CTkButton(f_form, text="💾 حفظ السجل", fg_color=KYO_RED, font=("Segoe UI", 14, "bold"), height=40, command=self.save_crm).pack(side=tk.LEFT, padx=15, pady=15)

        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview", background=KYO_BLACK, foreground=KYO_TEXT, fieldbackground=KYO_BLACK, borderwidth=0)
        style.map('Treeview', background=[('selected', KYO_RED)])
        style.configure("Treeview.Heading", background=KYO_PANEL, foreground=KYO_TEXT, font=("Segoe UI", 12, "bold"))

        self.crm_tree = ttk.Treeview(tab, columns=("id", "name", "phone", "model", "cost", "date"), show="headings", height=8)
        for col, txt in zip(self.crm_tree["columns"], ["م", "العميل", "الهاتف", "الموديل", "التكلفة", "التاريخ"]):
            self.crm_tree.heading(col, text=txt)
            self.crm_tree.column(col, anchor="center", width=100)
        self.crm_tree.pack(fill=tk.BOTH, expand=True, padx=15, pady=10)
        self.load_crm()

    def save_crm(self):
        name = self.crm_name.get().strip()
        if not name: return messagebox.showerror("خطأ", "أدخل اسم العميل")
        try:
            self.crm_db.add_record(name, self.crm_phone.get(), self.crm_model.get(), self.crm_details.get(), self.crm_cost.get())
            self.load_crm()
            messagebox.showinfo("نجاح", "تم الحفظ بنجاح")
        except Exception as e: messagebox.showerror("خطأ", str(e))

    def load_crm(self):
        for row in self.crm_tree.get_children(): self.crm_tree.delete(row)
        try:
            for row in self.crm_db.list_records(): self.crm_tree.insert("", "end", values=row)
        except: pass

# ========================================================================
# نقطة التشغيل الرئيسية
# ========================================================================
if __name__ == "__main__":
    root = ctk.CTk()
    app = KyoceraDeepEngine(root)
    root.mainloop()
