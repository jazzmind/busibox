# ChartParser: Automatic Chart Parsing for Print-Impaired
- **ID:** `doc03_chartparser_paper`
- **Type:** Academic Paper (CS)
- **Source URL:** https://arxiv.org/pdf/2211.08863.pdf

## Expected Content Elements
- Title, author list, affiliations
- Abstract
- Sectioned body (Introduction, Method, Experiments, Results, Conclusion)
- Equations or pseudo-code (if present)
- Figure(s) showing pipeline overview
- Tables of quantitative results

## Key Details
- Presents ChartParser, a pipeline to extract figures from scientific PDFs and convert bar charts into accessible, tabular data.
- Pipeline stages: (1) figure extraction from PDF, (2) chart type classification, (3) bar chart content extraction.
- Evaluated on real-world research-paper charts; reports accuracy for detecting chart components and extracting data.

## Semantic Themes
- Accessibility for blind/low-vision users
- Computer vision and document analysis
- Deep-learning-based chart understanding

## Layout / Structural Features
- Two-column conference/workshop layout.
- Numbered sections and subsections.
- References section with citation list at the end.

## Images and Diagrams
At least one main figure depicts the three-step ChartParser pipeline as a flow diagram; other figures may include example charts with detected elements annotated.

## Tables
Contains tables summarizing chart classification and content-extraction accuracy by component and by dataset.

## Extraction Checklist
- Are the two columns merged in the correct reading order (top-left to bottom-left, then top-right to bottom-right)?
- Is the abstract captured as one contiguous segment labeled as such?
- Are table structures (headers, rows, columns) preserved with numerical values intact?
- Are figure captions linked to the correct figure numbers?
- Are section headers (e.g., 1 Introduction, 2 Methodology) captured correctly and not merged with body text?

## Difficulty Rating

- high