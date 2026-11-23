# PaperOrchestra Local (español)

**Escribe y revisa papers científicos con modelos locales: sin llaves de API, sin nube.**

> Complemento NO oficial construido sobre [google-research/paper-orchestra](https://github.com/google-research/paper-orchestra) (Apache-2.0). Sin afiliación con Google. Ver [NOTICE](NOTICE).
> *English: [README.md](README.md)*

Dos aplicaciones comparten el mismo backend de modelos locales (cualquier servidor compatible con OpenAI: Ollama, llama.cpp, LM Studio, vLLM...):

| App | Comando | Qué hace |
|---|---|---|
| **Escribir** | `python write_paper_local.py` | Le das una idea + resultados (+ figuras + una biblioteca de referencias) y escribe el paper: esquema → literatura offline → borrador LaTeX → PDF. |
| **Revisar** | `python review_paper.py PAPER` | Funciona con **cualquier** paper (`.tex`, carpeta, `.zip` de Overleaf o solo `.pdf`). Encuentra problemas, **reescribe lo necesario con justificación**, deja el resto como sugerencias y lo muestra en vivo en una GUI sobre tu PDF. |

## Qué lo hace distinto
- **Proponer barato, verificar caro**: un modelo pequeño filtra todo; uno mayor solo adjudica lo discutible, con un único cambio de modelo (clave en 16 GB de RAM).
- **Críticas ancladas**: cada hallazgo debe citar el texto literal; las críticas inventadas se descartan y se cuentan.
- **Cómputo según riesgo** y rondas adversariales "Revisor 2".
- **Torneo de parches** con orden A/B y B/A. **Vinculación de evidencia**: los números del paper que no están en tu log se marcan al instante.
- **Coherencia**: tras suavizar una afirmación, se busca la misma en otras secciones y se reescribe de forma consistente.
- **ETA honesta** con velocidades medidas en vivo.

### Garantías científicas (probadas con tests)
Un cambio se aplica solo si es *necesario* **y** mantiene **exactamente los mismos números**, no añade citas ni referencias, conserva el balance de LaTeX, no toca tablas/matemáticas y gana el torneo. Lo demás queda como sugerencia aplicable con un clic. En modo solo-PDF no se reescribe nada.

## Inicio rápido
Requisitos: Python 3.10+, un servidor local (se asume [Ollama](https://ollama.com)) y `pdflatex` (MiKTeX / TeX Live).

```bash
git clone https://github.com/<tu-usuario>/paperorchestra-local.git
cd paperorchestra-local
python -m venv .venv && .venv\Scripts\activate        # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python scripts/dry_run.py                              # 1) sin modelo: comprueba que todo está bien cableado
```

Con modelo real (Windows: `setx OLLAMA_CONTEXT_LENGTH 16384` y reinicia Ollama):

```bash
ollama pull qwen2.5:3b-instruct
ollama pull qwen2.5:7b-instruct
python write_paper_local.py --model qwen2.5:7b-instruct --ctx 16384
python review_paper.py mi_paper.zip --fast-model qwen2.5:3b-instruct --deep-model qwen2.5:7b-instruct
```

Vista previa de la GUI sin modelo sobre cualquier paper: `python review_paper.py paper.pdf --diagnostic`.

## Estado y límites honestos
Alfa (v0.1.0). **Probado**: 25 tests automáticos (unitarios y de extremo a extremo con un servidor de modelo falso y `pdflatex` real). **Sin medir**: la calidad con modelos reales de 3B-8B y los tiempos en tu equipo. Los modelos de texto no ven las figuras; los PDF escaneados necesitan OCR; las autoevaluaciones del modelo sirven para priorizar, no predicen aceptación.

## Seguridad
Todo corre en tu máquina. **Nunca subas `.env`, llaves ni manuscritos inéditos** (el `.gitignore` los excluye).

Documentación (en inglés): [Uso](docs/USAGE.md) · [Modelos locales](docs/LOCAL_MODELS.md) · [Arquitectura](docs/ARCHITECTURE.md) · [GUI](docs/GUI.md) · [Problemas comunes](docs/TROUBLESHOOTING.md)

## Licencia
Apache-2.0 ([LICENSE](LICENSE), [NOTICE](NOTICE)).
