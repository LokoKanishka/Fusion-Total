import pexpect
import sys
import time

def main():
    print("--- EMULADOR DE USUARIO (PEXPECT) ---")
    child = pexpect.spawn("python3 scripts/reader_cli.py diego_audio", encoding="utf-8", timeout=25)
    child.logfile = sys.stdout # Log everything to stdout!

    child.expect("Bloque Uno", timeout=10)
    
    # Wait for the auto-advance second chunk
    child.expect("Bloque Dos", timeout=10)
    
    # Wait just a bit while it reads chunk 2, then interrupt
    time.sleep(1)
    child.sendline("") # empty line causes pause/barge-in
    
    # Expect prompt back
    child.expect(">", timeout=5)
    
    time.sleep(1)
    child.sendline("¿qué quiso decir acá?")
    
    # Expect system response
    child.expect("SISTEMA RESPONDE", timeout=10)
    
    time.sleep(1)
    child.sendline("resumime este bloque")
    
    child.expect("SISTEMA RESPONDE", timeout=10)

    time.sleep(1)
    child.sendline("seguí")

    # Expect it to read next chunk naturally because "seguí" triggers next or resume
    # 'seguí' resumes chunk 2 because it was interrupted!
    child.expect("Bloque Dos", timeout=10)

    time.sleep(1)
    child.sendline("andá al párrafo 3")
    
    child.expect("Bloque Tres", timeout=10)

    time.sleep(1)
    child.sendline("pará")
    child.expect(">", timeout=5)

    child.sendline("salir")
    child.close()
    print("\\n--- FIN TEST ---")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("\\nError en pexpect:", e)
