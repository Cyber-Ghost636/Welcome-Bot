import os

BUNGIE_API_KEY = os.getenv("BUNGIE_API_KEY", "")

# Channel used by the daily lost sector announcer.
ANNOUNCEMENT_CHANNEL_ID = int(os.getenv("LOST_SECTOR_ANNOUNCEMENT_CHANNEL_ID", "0"))

LOST_SECTOR_MAP = {
    # The Pale Heart
    3097361111: "The Blooming Deep (The Pale Heart)",
    1823906233: "The Broken Deep (The Pale Heart)",
    2409581177: "The Forgotten Deep (The Pale Heart)",

    # Neomuna
    2943314115: "Hydroponics Delta (Neomuna)",
    3966571556: "Gilded Precept (Neomuna)",
    3342345511: "Thrilladrome (Neomuna)",

    # Savathun's Throne World
    1421715509: "Metamorphosis (Throne World)",
    4197314560: "Sepulcher (Throne World)",
    2894109403: "Extraction (Throne World)",

    # Europa
    3936551608: "Bunker E15 (Europa)",
    2898516048: "Concealed Void (Europa)",
    1150536411: "Perdition (Europa)",

    # The Moon
    2749444400: "K1 Logistics (The Moon)",
    3316104847: "K1 Crew Quarters (The Moon)",
    1956131154: "K1 Communion (The Moon)",
    1142163162: "K1 Revelation (The Moon)",

    # Cosmodrome
    2901011155: "Exodus Garden 2A (Cosmodrome)",
    3253878256: "Veles Labyrinth (Cosmodrome)",

    # Dreaming City
    1550982542: "Bay of Drowned Wishes (Dreaming City)",
    1408332152: "Chamber of Starlight (Dreaming City)",
    2593641400: "Aphelion's Rest (Dreaming City)",

    # Nessus
    1320491177: "The Orrery (Nessus)",
    2103561102: "The Rift (Nessus)",
    3411095513: "The Carrion Pit (Nessus)",
    2309101147: "The Conflux (Nessus)",
    1845112294: "Ancient's Haunt (Nessus)",

    # European Dead Zone (EDZ)
    3751145110: "Terminus East (EDZ)",
    1984210044: "Atrium (EDZ)",
    2330541162: "Widow's Walk (EDZ)",
    3125410931: "Scavenger's Den (EDZ)",
    4005116641: "The Drain (EDZ)",
    1241103362: "Whispered Falls (EDZ)",
    2951103347: "The Weep (EDZ)",
    1841102293: "Pathfinder's Crash (EDZ)",
    2601053154: "Excavation Site XII (EDZ)",
    3121045511: "The Pit (EDZ)",
    1101036642: "Skydock IV (EDZ)",
    1342168256: "The Quarry (EDZ)",
    2401032152: "Flooded Chasm (EDZ)",
    3501062542: "Cavern of Souls (EDZ)",
    1650941177: "Shaft 13 (EDZ)",
}
