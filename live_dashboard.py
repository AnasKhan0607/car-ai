import obd
import time
import os

print("Connecting to OBD adapter...")
connection = obd.OBD()

if not connection.is_connected():
    print("No OBD adapter found. Check connection and try again.")
    exit()

print("Connected! Starting live dashboard...\n")
time.sleep(1)

RESIDENTIAL_SPEED_LIMIT_KMH = 70
RESIDENTIAL_RPM_LIMIT = 4000

try:
    while True:
        os.system('clear')
        print("=" * 45)
        print("       LIVE CAR DASHBOARD")
        print("=" * 45)

        speed_resp = connection.query(obd.commands.SPEED)
        rpm_resp = connection.query(obd.commands.RPM)
        coolant = connection.query(obd.commands.COOLANT_TEMP)
        fuel = connection.query(obd.commands.FUEL_LEVEL)
        load = connection.query(obd.commands.ENGINE_LOAD)

        speed_kmh = None
        rpm_val = None

        if not speed_resp.is_null():
            speed_kmh = speed_resp.value.to("kph").magnitude
            print(f"Speed:         {speed_kmh:.0f} km/h")
        else:
            print("Speed:         N/A")

        if not rpm_resp.is_null():
            rpm_val = rpm_resp.value.magnitude
            print(f"RPM:           {rpm_val:.0f}")
        else:
            print("RPM:           N/A")

        print(f"Coolant Temp:  {coolant.value if not coolant.is_null() else 'N/A'}")
        print(f"Fuel Level:    {fuel.value if not fuel.is_null() else 'N/A'}")
        print(f"Engine Load:   {load.value if not load.is_null() else 'N/A'}")

        print("-" * 45)

        if speed_kmh is not None and speed_kmh > RESIDENTIAL_SPEED_LIMIT_KMH:
            print(f"CAUTION: Speed over {RESIDENTIAL_SPEED_LIMIT_KMH} km/h - too fast for residential/commercial roads")

        if rpm_val is not None and rpm_val > RESIDENTIAL_RPM_LIMIT:
            print(f"CAUTION: RPM over {RESIDENTIAL_RPM_LIMIT} - high engine speed for residential/commercial roads")

        print("=" * 45)
        print("Press Ctrl+C to stop")

        time.sleep(2)

except KeyboardInterrupt:
    print("\nDashboard stopped.")
