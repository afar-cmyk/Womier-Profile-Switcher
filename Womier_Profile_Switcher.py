import tkinter as tk
from tkinter import messagebox
import json
import hid
import time
import threading
import os
import sys
from pynput import keyboard
from PIL import Image
from pystray import MenuItem as item, Icon

# --- FUNCIÓN AUXILIAR PARA ENCONTRAR ARCHIVOS (CRUCIAL PARA EL .EXE) ---
def resource_path(relative_path):
    """ Obtiene la ruta absoluta al recurso, funciona para dev y para PyInstaller """
    try:
        # PyInstaller crea una carpeta temporal y guarda la ruta en _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

# --- CONFIGURACIÓN ---
VENDOR_ID = 3141
PRODUCT_ID = 32869
USAGE_PAGE = 65384
USAGE = 97
PROFILES_DIR = os.path.join(os.path.dirname(sys.argv[0]), "profiles")
ICON_FILE = resource_path("icon.png")

# --- AJUSTE DE VELOCIDAD (0 = MÁXIMA VELOCIDAD) ---
PACKET_DELAY_SECONDS = 0

# --- CONFIGURACIÓN DEL EFECTO DE FEEDBACK ---
# Esta pausa es la que crea la "ilusión de instantaneidad"
FLASH_DURATION_SECONDS = 0.15

# --- CONFIGURACIÓN DEL ATAJO DE TECLADO ---
# Cambia esta línea si quieres una combinación diferente
HOTKEY_COMBINATION = '<ctrl>+<alt>+0'

def send_data_block(device, command_id, data_array):
    """Envía un bloque de datos al teclado en trozos a máxima velocidad."""
    data_array = list(data_array)
    data_sanitized = [min(max(0, val), 255) for val in data_array]
    chunk_size = 56
    for i in range(0, len(data_sanitized), chunk_size):
        offset = i
        chunk = data_sanitized[offset:offset + chunk_size]
        packet = [0x00] * 65
        packet[1:6] = [170, command_id, len(chunk), offset & 0xFF, (offset >> 8) & 0xFF]
        packet[9:9+len(chunk)] = chunk
        device.write(bytes(packet))
        if PACKET_DELAY_SECONDS > 0:
            time.sleep(PACKET_DELAY_SECONDS)

def set_keyboard_color_solid(device, r, g, b):
    """Envía un único comando de identidad para poner todo el teclado de un color sólido."""
    paquete_id = [0x00] * 65
    paquete_id[1:4] = [170, 35, 16]
    paquete_id[9], paquete_id[17] = 1, 0
    paquete_id[10:13] = [r, g, b]
    paquete_id[18] = 5
    device.write(bytes(paquete_id))

def aplicar_perfil(perfil_data, app_instance):
    """Orquesta la secuencia: conectar, flash de color inmediato, y luego carga de datos completa."""
    device = None
    profile_name = perfil_data.get('name', 'Unknown Profile')
    
    try:
        device_info = hid.enumerate(VENDOR_ID, PRODUCT_ID)
        path = next((d['path'] for d in device_info if d['usage_page'] == USAGE_PAGE and d['usage'] == USAGE), None)
        if not path:
            app_instance.update_status("Error: Teclado no encontrado.")
            return

        device = hid.device()
        device.open_path(path)
        
        flash_color = [
            perfil_data.get("RValue", 255),
            perfil_data.get("GValue", 255),
            perfil_data.get("BValue", 255)
        ]
        set_keyboard_color_solid(device, *flash_color)
        time.sleep(FLASH_DURATION_SECONDS)
        
        print(f"Cargando datos para '{profile_name}' en segundo plano...")
        send_data_block(device, 34, perfil_data.get('allKeyPack', []))
        send_data_block(device, 38, perfil_data.get('allFnPack', []))
        send_data_block(device, 36, perfil_data.get('allledPack', []))
        send_data_block(device, 39, perfil_data.get('RTlist', []))
        send_data_block(device, 40, perfil_data.get('allDksPack', []))
        send_data_block(device, 37, perfil_data.get('allMarcoPack', []))
    
        set_keyboard_color_solid(device, *flash_color)
        
        status_message = f"Perfil activo: {profile_name}"
        app_instance.update_status(status_message)
        print(status_message)

    except Exception as e:
        error_message = f"Error al aplicar '{profile_name}': {e}"
        print(error_message)
        app_instance.update_status(error_message)
    finally:
        if device:
            device.close()

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Womier Profile Switcher")
        self.root.geometry("300x150")
        self.root.resizable(False, False)
        
        try:
            self.icon = tk.PhotoImage(file=ICON_FILE)
            self.root.iconphoto(True, self.icon)
        except tk.TclError:
            print("Advertencia: No se encontró 'icon.png'. Se usará el icono por defecto.")
            self.icon = None

        self.profiles = []
        self.profile_buttons = []
        self.current_profile_index = -1
        self.hotkey_listener = None
        self.tray_icon = None
        
        # self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        # atexit.register(self.on_closing)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)        
        
        self.setup_ui()
        self.load_profiles()
        self.setup_hotkeys()
        
        threading.Thread(target=self.setup_tray_icon, daemon=True).start()
        self.root.withdraw()
        
    def hide_window(self):
        """Oculta la ventana principal."""
        self.root.withdraw()
        print("Aplicación minimizada a la bandeja del sistema.")

    def show_window(self, icon, item):
        """Muestra la ventana principal desde la bandeja."""
        self.root.after(0, self.root.deiconify)

    def quit_app(self, icon, item):
        """Cierra la aplicación de forma segura."""
        print("Deteniendo listener de atajos...")
        if self.hotkey_listener:
            self.hotkey_listener.stop()
        self.tray_icon.stop()
        self.root.destroy()
        
    def setup_tray_icon(self):
        """Configura y ejecuta el icono de la bandeja del sistema."""
        try:
            image = Image.open(ICON_FILE)
            menu = (item('Mostrar', self.show_window), item('Salir', self.quit_app))
            self.tray_icon = Icon("WomierSwitcher", image, "Womier Profile Switcher", menu)
            self.tray_icon.run()
        except FileNotFoundError:
            print("ADVERTENCIA: No se encontró 'icon.png'. El icono de la bandeja no funcionará.")
            # Si no hay icono, al menos asegúrate de que el cierre funcione bien
            self.root.protocol("WM_DELETE_WINDOW", self.on_closing)    

    def setup_ui(self):
        main_frame = tk.Frame(self.root, padx=10, pady=10)
        main_frame.pack(fill="both", expand=True)
        
        self.label = tk.Label(main_frame, text=f'Cambiar Perfil: {HOTKEY_COMBINATION}')
        self.label.pack(pady=(0, 5), anchor='w')
        
        self.label = tk.Label(main_frame, text="Perfiles Disponibles:")
        self.label.pack(pady=(0, 5), anchor='w')
        
        self.profiles_frame = tk.Frame(main_frame)
        self.profiles_frame.pack(fill="x", expand=True)

        self.status_var = tk.StringVar(value="Listo.")
        status_label = tk.Label(self.root, textvariable=self.status_var, bd=1, relief=tk.SUNKEN, anchor=tk.W, padx=5)
        status_label.pack(side=tk.BOTTOM, fill=tk.X)

    def load_profiles(self):
        if not os.path.exists(PROFILES_DIR):
            os.makedirs(PROFILES_DIR)
        
        for widget in self.profiles_frame.winfo_children():
            widget.destroy()

        profile_files = sorted([f for f in os.listdir(PROFILES_DIR) if f.endswith('.json')])
        
        self.profiles = []
        self.profile_buttons = []

        if not profile_files:
            tk.Label(self.profiles_frame, text=f"No hay perfiles en '{PROFILES_DIR}'.").pack()
            return

        for filename in profile_files:
            try:
                profile_path = os.path.join(PROFILES_DIR, filename)
                with open(profile_path, 'r', encoding='utf-8') as f:
                    profile_data = json.load(f)
                
                profile_name = os.path.splitext(filename)[0]
                profile_data['name'] = profile_name
                self.profiles.append(profile_data)
                
                button = tk.Button(
                    self.profiles_frame, 
                    text=profile_name,
                    command=lambda data=profile_data, idx=len(self.profiles)-1: self.apply_profile_thread(data, idx)
                )
                button.pack(pady=3, padx=10, fill='x', anchor='w')
                self.profile_buttons.append(button)
            except Exception as e:
                print(f"Error cargando el perfil '{filename}': {e}")
        
        if self.profiles:
            self.current_profile_index = -1

    def switch_to_next_profile(self):
        if not self.profiles:
            print("No hay perfiles cargados para cambiar.")
            return

        next_index = (self.current_profile_index + 1) % len(self.profiles)
        profile_to_load = self.profiles[next_index]
        self.apply_profile_thread(profile_to_load, next_index)

    def setup_hotkeys(self):
        hotkeys = {HOTKEY_COMBINATION: self.switch_to_next_profile}
        self.hotkey_listener = keyboard.GlobalHotKeys(hotkeys)
        self.hotkey_listener.start()
        print(f"Atajo global '{HOTKEY_COMBINATION}' activado.")

    def apply_profile_thread(self, profile_data, index):
        self.current_profile_index = index
        self.update_active_button_highlight()
        self.toggle_buttons('disabled')
        
        thread = threading.Thread(target=self.run_and_reenable, args=(profile_data,))
        thread.start()

    def run_and_reenable(self, profile_data):
        aplicar_perfil(profile_data, self)
        self.root.after(0, self.toggle_buttons, 'normal')

    def update_active_button_highlight(self):
        """Resalta el botón del perfil activo."""
        bg_color = self.root.cget('bg')
        for i, button in enumerate(self.profile_buttons):
            if i == self.current_profile_index:
                button.config(highlightbackground='#4CAF50', highlightthickness=2) # Verde
            else:
                button.config(highlightbackground=bg_color, highlightthickness=0)
    
    def toggle_buttons(self, state):
        for widget in self.profiles_frame.winfo_children():
            if isinstance(widget, tk.Button):
                widget.config(state=state)
    
    def update_status(self, message):
        self.status_var.set(message)
        
    def quit_app_legacy(self):
        print("Deteniendo listener de atajos y cerrando...")
        if self.hotkey_listener:
            self.hotkey_listener.stop()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()