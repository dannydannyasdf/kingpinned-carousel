# Kingpinned Carousel / Turntable Matrix Navigator

A 3D visualization tool for time-ordered records from ClickHouse, displaying data as a "carousel" around a time spindle with dual-focus interaction (graph and terminal).

## Features

- **3D Visualization**: Navigate through time-ordered data with interactive 3D carousel
- **Dual Input Focus**: Switch between GRAPH (arrow navigation) and TERMINAL (command input) modes
- **Selection Tools**: Multi-selection with Shift/Ctrl combinations for batch operations
- **Real-time Updates**: Live data monitoring with configurable refresh rates
- **Export Options**: Save selections as CSV, JSON, or text files

## Linux Setup (Recommended)

### Prerequisites

- **Python 3.8+** (Python 3.10+ recommended)
- **ClickHouse client** (optional, for database connectivity)

### Quick Setup

The easiest way to get started is using the provided setup script:

```bash
# Clone and enter repository
git clone <repository-url>
cd kingpinned-carousel

# Run setup script (creates venv, installs deps, runs app)
./run.sh
```

### Manual Setup (Advanced)

If you prefer manual setup or need to troubleshoot:

1. **Create Virtual Environment** (avoids PEP 668 "externally-managed-environment" issues):
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. **Install Dependencies**:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. **Configure ClickHouse** (optional):
   ```bash
   # Set your ClickHouse password
   export CH_PASSWORD='your_password_here'
   
   # Test ClickHouse connectivity
   clickhouse-client --password "$CH_PASSWORD" --query "SELECT 1"
   ```

4. **Run the Application**:
   ```bash
   python3 turntable_keysGit96.py
   ```

### Installing ClickHouse Client

**Ubuntu/Debian**:
```bash
sudo apt-get update
sudo apt-get install clickhouse-client
```

**Other Linux Distributions**:
- **CentOS/RHEL/Fedora**: Use `yum` or `dnf` to install `clickhouse-client`
- **Arch Linux**: Use `pacman -S clickhouse`
- **Manual Installation**: Download from [ClickHouse releases](https://github.com/ClickHouse/ClickHouse/releases)

## Usage

### Key Controls (GRAPH Focus)

- **TAB**: Toggle between GRAPH and TERMINAL focus
- **Arrow Keys**: Navigate through data points
- **Shift + Up/Down**: Extend contiguous block selection
- **Shift + Left/Right**: Extend character range in current block
- **Ctrl + Space**: Toggle current ordinal in aggregated selection
- **Space**: Toggle current character or character-range for current ordinal
- **Enter**: Open Selection Console for batch operations
- **R**: Reset camera and selection window
- **F6/F7/F8**: Cycle color palette / adjust terminal font zoom
- **U**: Toggle truncation mode
- **Q**: Quit application

### Terminal Commands (TERMINAL Focus)

- `goto <ordinal|time>` - Jump to specific record or time
- `range <start>-<end>` - View range of records
- `pattern <search>` - Search for patterns in data
- `save`, `csv`, `json` - Export current selection
- `spokes <int>` - Adjust visualization spokes
- `turns <float>` - Adjust carousel turns
- `help` - Show available commands

### Environment Variables

- **CH_PASSWORD**: ClickHouse database password (default: 'asdf')
- **Display**: Ensure DISPLAY is set for GUI (X11 forwarding for SSH)

## Troubleshooting

### Common Issues

**1. PEP 668 "externally-managed-environment" Error**
```bash
# Solution: Use virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**2. Matplotlib GUI Backend Issues**
```bash
# Install GUI backend packages
sudo apt-get install python3-tk          # Tkinter backend
# OR
sudo apt-get install python3-pyqt5       # Qt5 backend
```

**3. "clickhouse-client not found" Warning**
- The application will still run with limited functionality
- Install ClickHouse client or ignore if not using database features

**4. CH_PASSWORD Not Set Warning**
- Set the environment variable: `export CH_PASSWORD='your_password'`
- Or use the default 'asdf' for testing

**5. Wrong File Name Error**
- Ensure you're running: `python3 turntable_keysGit96.py`
- Check current directory: `ls -la *.py`

**6. Virtual Environment Not Active**
```bash
# Check if venv is active (should show (.venv) in prompt)
which python3

# Activate if needed
source .venv/bin/activate
```

### Verification Commands

**Check Dependencies**:
```bash
python3 -c "import numpy, matplotlib; print('Dependencies OK')"
```

**Verify File Line Count**:
```bash
wc -l turntable_keysGit96.py    # Should show ~1800+ lines
```

**Test ClickHouse Connection**:
```bash
clickhouse-client --password "$CH_PASSWORD" --query "SELECT 1"
```

## Development

### Project Structure
```
kingpinned-carousel/
├── turntable_keysGit96.py      # Main application
├── turntable_findrecord.sh     # ClickHouse record finder utility
├── requirements.txt            # Python dependencies
├── run.sh                     # Setup and run script
├── README.md                  # This file
└── .gitignore                 # Git ignore patterns
```

### Dependencies
- **numpy>=1.24**: Numerical computations and array operations
- **matplotlib>=3.7**: 3D plotting and interactive visualization

## License

[Add license information here]

## Contributing

[Add contribution guidelines here]