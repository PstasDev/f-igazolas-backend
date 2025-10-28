from datetime import datetime

def erintett_tanorak(eleje: datetime, vege: datetime) -> list[int]:
    # megmondja, mely órák érintettek két időopont között
    orak = [
        ("07:30", "08:15"), # 0. óra
        ("08:25", "09:10"), # 1. óra
        ("09:20", "10:05"), # 2. óra
        ("10:20", "11:05"), # 3. óra
        ("11:15", "12:00"), # 4. óra
        ("12:20", "13:05"), # 5. óra
        ("13:25", "14:10"), # 6. óra
        ("14:20", "15:05"), # 7. óra
        ("15:15", "16:00"), # 8. óra
    ]

    erintett = []

    for i, (kezdes_str, veg_str) in enumerate(orak):
        kezdes_ido = datetime.combine(eleje.date(), datetime.strptime(kezdes_str, "%H:%M").time())
        veg_ido = datetime.combine(eleje.date(), datetime.strptime(veg_str, "%H:%M").time())

        if eleje < veg_ido and vege > kezdes_ido:
            erintett.append(i)
    
    return erintett