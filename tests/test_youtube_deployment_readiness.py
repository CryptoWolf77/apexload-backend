from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_clean_docker_build_contains_pinned_youtube_runtime() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert "ARG DENO_VERSION=2.9.5" in dockerfile
    assert "ARG BGUTIL_PROVIDER_VERSION=1.3.1" in dockerfile
    assert "yt-dlp[default,curl-cffi]==2026.07.04" in requirements
    assert "yt-dlp-ejs==0.8.0" in requirements
    assert "bgutil-ytdlp-pot-provider==1.3.1" in requirements
    assert 'CMD ["python", "start_services.py"]' in dockerfile


def test_bgutils_is_forced_to_loopback_and_only_backend_port_is_exposed() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    expose_lines = [
        line.strip() for line in dockerfile.splitlines()
        if line.strip().upper().startswith("EXPOSE ")
    ]

    assert expose_lines == ["EXPOSE 8000"]
    assert "sed -i" in dockerfile
    assert dockerfile.count('host: \"127.0.0.1\"') >= 3
    assert "! grep -Eq" in dockerfile


def test_runtime_has_no_warp_or_diagnostic_proxy_dependency() -> None:
    runtime_files = [
        ROOT / "Dockerfile",
        ROOT / "start_services.py",
        ROOT / "requirements.txt",
        *sorted((ROOT / "app").rglob("*.py")),
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in runtime_files).lower()

    assert "apexload-warp" not in combined
    assert "176.65.140.214" not in combined
    assert "http_proxy" not in combined
    assert "https_proxy" not in combined
    assert "all_proxy" not in combined
