# DoD SkillBridge Opportunity Finder

A command-line tool for active-duty military members to search and filter DoD SkillBridge authorized organizations — with industry tagging, keyword search, and MOU expiration dates.

---

## Quick Start

```bash
git clone https://github.com/CARLOSFUN/skillbridge-finder.git
cd skillbridge-finder
./run.sh
```

That's it. The launcher creates the virtual environment and installs dependencies automatically on the first run. Every run after that is instant.

---

## Demo

```
$ ./run.sh
```

```
  ███████╗██╗  ██╗██╗██╗     ██╗     ██████╗ ██████╗ ...
  ...
          DoD SkillBridge Authorized Organizations — Opportunity Finder

  Industry categories:

     1. Aerospace / Aviation
     2. Technology / IT / Cyber
     3. Defense / Intelligence
     4. Healthcare / Medical
     5. Logistics / Supply Chain
     ...
    12. Other

  Pick an industry number (or press Enter to skip): 4
  Keyword in company name (or Enter to skip):
  State code (e.g. TX, CA — or Enter to skip): TX

  Found 12 organization(s):

     # | Organization Name           | Industry             | Website                  | MOU Expires
  --------------------------------------------------------------------------------------------------
     1 | Baylor Scott & White Health | Healthcare / Medical | https://www.bswhealth.com | 2026-12-19
     2 | Methodist Healthcare System | Healthcare / Medical | https://www.joinmethodi…  | 2027-05-02
  ...

  Save to file? (Enter a filename like results.csv, or press Enter to skip):
```

---

## All Commands

| Command | What It Does |
|---|---|
| `./run.sh` | Interactive guided mode (pick industry, search, filter) |
| `skillbridge --list-industries` | Show all 12 industry categories |
| `skillbridge --industry "Technology"` | Filter by industry |
| `skillbridge --search "amazon"` | Search by keyword in company name |
| `skillbridge --state TX` | Filter by state |
| `skillbridge --industry "Healthcare" --state CA` | Combine filters |
| `skillbridge -o results.csv` | Save results to a CSV file |
| `skillbridge --industry "Logistics" -o out.csv` | Filter and save |
| `skillbridge --format json -o out.json` | Save as JSON |
| `skillbridge --refresh` | Force fresh download from DoD API |
| `skillbridge --help` | Show all available options |

> Partial industry matches work: `--industry "tech"` and `--industry "Technology"` both work.

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

## Files

```
scrape_skillbridge.py   — main program
requirements.txt        — Python dependencies
run.sh                  — launcher (handles setup automatically)
```

---

## How It Works

Data is pulled from the official DoD SkillBridge API (`api.skillbridge.mil`). Results are cached locally for 7 days so repeat searches are instant. Industry categories are auto-detected from company names using regex pattern matching.

---

## Contributing

Pull requests welcome. If you add new industry keywords or find a bug, open an issue or submit a PR.

---

*Built for the military community. Not affiliated with the DoD or SkillBridge program.*
