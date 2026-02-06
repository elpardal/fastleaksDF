import re
from pathlib import Path
from typing import List, Dict
from shared.config import settings


class IOCMatcher:
    def __init__(self):
        self.patterns: Dict[str, re.Pattern] = {
            "cpf": re.compile(settings.ioc_patterns_cpf),
            "email_gdf": re.compile(settings.ioc_patterns_email),
            "domain_df": re.compile(settings.ioc_patterns_domain),
            "ip_internal": re.compile(settings.ioc_patterns_ip_internal),
            "credentials": re.compile(r"(?i)(password|senha|passwd)[\s:=\"']{0,3}([A-Za-z0-9@#$%^&*()_+\-={}\[\]:;\"'<>,.?/\\|`~]{8,})"),
        }

    def scan_file(self, file_path: str, max_size_mb: int = 10) -> List[Dict]:
        matches = []
        path = Path(file_path)
        
        if not path.exists() or path.stat().st_size > max_size_mb * 1024 * 1024:
            return matches

        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            for line_num, line in enumerate(lines, 1):
                for ioc_type, pattern in self.patterns.items():
                    for match in pattern.finditer(line):
                        start = max(0, line_num - 3)
                        end = min(len(lines), line_num + 2)
                        context = "\n".join(
                            f"{'>' if i+1 == line_num else ' '} {i+1:4d} | {lines[i].rstrip()}"
                            for i in range(start, end)
                        )
                        matches.append({
                            "ioc_type": ioc_type,
                            "value": match.group(0),
                            "line_number": line_num,
                            "context": context,
                        })
        except Exception:
            pass  # Arquivo binário ou encoding problemático → skip silencioso
        
        return matches


ioc_matcher = IOCMatcher()