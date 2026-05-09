# Paludoro
A productivity-focused virtual pet.

This project combines virtual pets with Pomodoro focus sessions.

## Development

### Prerequisites
- Python >= 3.11
- PDM
- Node.js & npm

### Setup
1. Clone the repository.
2. Install Python dependencies:
   ```bash
   pdm install
   ```
3. Install web dependencies:
   ```bash
   pdm run web-install
   ```
4. Build the web frontend:
   ```bash
   pdm run web-build
   ```

### Running the Server
```bash
pdm run server
```
The server will be available at `http://localhost:8000`.

### Building for Production
The `pdm run web-build` command generates the static assets in the `dist/` directory, which are then served by the FastAPI backend.
