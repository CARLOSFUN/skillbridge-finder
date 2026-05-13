# DoD SkillBridge Opportunity Finder

A command-line tool for active-duty military members to search, filter, and directly contact DoD SkillBridge employers — with full opportunity details including employer email, location, duration, eligible branches, target MOS/MOC codes, and job descriptions.

---

## Quick Start

```bash
git clone https://github.com/CARLOSFUN/skillbridge-finder.git
cd skillbridge-finder
./run.sh
```

The launcher creates the virtual environment and installs dependencies automatically on the first run. Every run after that is instant.

---

## Demo

https://github.com/CARLOSFUN/skillbridge-finder/raw/main/demo.mp4
<video src="https://github.com/CARLOSFUN/skillbridge-finder/raw/main/demo.mp4" controls width="600"></video>

---

## Two Search Modes

### Mode 1 — Organization Search
Browse the full catalog of 6,000+ DoD-authorized SkillBridge employers. Filter by industry or keyword.

### Mode 2 — Opportunity Search
Search 13,000+ individual program positions with rich details pulled from the SkillBridge Location API:

- **Employer contact email** — reach out directly to the hiring POC
- **City and state** — find opportunities near your base or next duty station
- **Duration** — see exactly how long each program runs
- **Eligible branches** — know if your branch qualifies before applying
- **Target MOS/MOC** — find opportunities matched to your military job code
- **Job description and summary** — read the full program description
- **Delivery method** — in-person, virtual, or hybrid

---

## All Commands

### Organization Search

| Command | What It Does |
|---|---|
| `./run.sh` | Interactive guided mode |
| `skillbridge --list-industries` | Show all 12 industry categories |
| `skillbridge --industry "Technology"` | Filter by industry |
| `skillbridge --search "amazon"` | Search by keyword in company name |
| `skillbridge -o results.csv` | Save all results to CSV |
| `skillbridge --industry "Logistics" -o out.csv` | Filter and save |
| `skillbridge --format json -o out.json` | Save as JSON |
| `skillbridge --refresh` | Force fresh download from DoD API |

### Opportunity Search

| Command | What It Does |
|---|---|
| `skillbridge --opportunities` | Show all 13,000+ opportunities with contact info |
| `skillbridge --opportunities --state CA` | Filter by state |
| `skillbridge --opportunities --state CA --city "San Diego"` | Filter by city and state |
| `skillbridge --opportunities --virtual` | Remote/online opportunities only |
| `skillbridge --opportunities --branch Army` | Filter by eligible branch |
| `skillbridge --opportunities --moc 25B` | Filter by MOS/MOC code |
| `skillbridge --opportunities --search "cyber"` | Keyword search in job descriptions |
| `skillbridge --opportunities -o ops.csv` | Save opportunity results to CSV |
| `skillbridge --opportunities --state TX --branch Army -o tx_army.csv` | Combine filters |
| `skillbridge --refresh --opportunities` | Force fresh download of opportunity data |

> Filters can be combined freely. City filter uses partial match — `--city "diego"` matches San Diego.

---

## Browsing Results

After any `--opportunities` search, the program displays a numbered results table. You can then browse individual listings interactively before saving:

```
  Found 12 opportunity/ies:

   #  | Organization            | Location       | Mode      | Duration       | Employer Email
  ----|-------------------------|----------------|-----------|----------------|----------------------
   1  | CACI International      | Chantilly, VA  | In-Person | 91 - 120 days  | john.smith@caci.com
   2  | Booz Allen Hamilton     | Virtual        | Virtual   | 151 - 180 days | jane.doe@bah.com
   ...

  Enter a row number to see full details, or press Enter to finish.

  Row # (or Enter to finish): 2
```

Each detail card shows:
- Full job description and program summary
- Employer name and direct email address
- City, state, and delivery method
- Eligible military branches
- Target MOS/MOC codes
- Eligibility requirements

Enter as many row numbers as you want. Press **Enter** with no input when you are done browsing — you will then be asked if you want to save the results to a file.

---

## Optional: Set Up a Global Shortcut

Run this once so you can type `skillbridge` from anywhere:

```bash
echo 'alias skillbridge="'$(pwd)'/run.sh"' >> ~/.zshrc
source ~/.zshrc
```

---

## Industry Categories

| # | Category | Examples |
|---|---|---|
| 1 | Aerospace / Aviation | Boeing, Lockheed, airlines |
| 2 | Technology / IT / Cyber | Software, cybersecurity, cloud, networking |
| 3 | Defense / Intelligence | Defense contractors, federal agencies |
| 4 | Healthcare / Medical | Hospitals, clinics, dental, pharma |
| 5 | Logistics / Supply Chain | Shipping, freight, warehouse, transport |
| 6 | Finance / Banking | Banks, insurance, investment, accounting |
| 7 | Engineering / Manufacturing | Industrial, construction, energy |
| 8 | Law Enforcement / Security | Police, corrections, protective services |
| 9 | Education / Training | Universities, colleges, academies |
| 10 | Consulting / Staffing | Consulting firms, staffing agencies |
| 11 | Retail / Hospitality | Retail chains, hotels, restaurants |
| 12 | Other | Everything else |

---

## How It Works

Data is pulled from two official DoD SkillBridge API endpoints:

- **`/Organizations/Authorized`** — full list of 6,000+ authorized employers with MOU expiration dates
- **`/Location/Lookup`** — 13,000+ individual program positions with employer contact info, location, branches, MOC targeting, and job descriptions

The opportunity search sweeps all 57 state/territory codes from the API to collect the complete dataset, then deduplicates by record ID. Results are cached locally for 7 days so repeat searches are instant.

---

## Files

```
scrape_skillbridge.py          — main program
requirements.txt               — Python dependencies
run.sh                         — launcher (handles setup automatically)
skillbridge_cache.json         — org search cache (auto-generated)
skillbridge_locations_cache.json — opportunity cache (auto-generated)
```

---

## Contributing

Pull requests welcome. If you find a bug or want to add features, open an issue or submit a PR.

---

*Built for the military community. Not affiliated with the DoD or SkillBridge program.*
