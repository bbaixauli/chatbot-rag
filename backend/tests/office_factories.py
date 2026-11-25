"""Documentos de Office y OpenDocument de prueba (#76), generados con las mismas librerías
que los leen."""

from datetime import date
from pathlib import Path

import docx
import pptx
from odf import dc, draw, style, table, text
from odf.namespaces import PRESENTATIONNS
from odf.opendocument import (
    OpenDocumentPresentation,
    OpenDocumentSpreadsheet,
    OpenDocumentText,
)
from openpyxl import Workbook


def make_docx(path: Path, title: str | None = None) -> Path:
    document = docx.Document()
    if title is not None:
        document.core_properties.title = title
    document.add_paragraph("Normas generales de la empresa.")
    document.add_heading("Teletrabajo", level=1)
    document.add_paragraph("Se puede teletrabajar dos días a la semana.")
    document.add_heading("Solicitud", level=2)
    document.add_paragraph("La solicitud se hace en el portal del empleado.")
    grid = document.add_table(rows=2, cols=3)
    for cell, value in zip(grid.rows[0].cells, ["Día", "Horario", "Lugar"], strict=True):
        cell.text = value
    for cell, value in zip(grid.rows[1].cells, ["Lunes", "8-15", "Casa"], strict=True):
        cell.text = value
    document.add_heading("Equipos", level=1)
    document.add_paragraph("La empresa proporciona el portátil.")
    document.save(str(path))
    return path


def make_pptx(path: Path, title: str | None = None) -> Path:
    presentation = pptx.Presentation()
    if title is not None:
        presentation.core_properties.title = title
    cover = presentation.slides.add_slide(presentation.slide_layouts[0])
    cover.shapes.title.text = "Plan de formación"
    cover.placeholders[1].text = "Curso 2026"

    content = presentation.slides.add_slide(presentation.slide_layouts[1])
    content.shapes.title.text = "Cursos obligatorios"
    content.placeholders[1].text = "Prevención de riesgos laborales"
    content.notes_slide.notes_text_frame.text = "Duración de ocho horas."
    group = content.shapes.add_group_shape()
    box = group.shapes.add_textbox(0, 0, 100, 100)
    box.text_frame.text = "Texto agrupado"
    grid = content.shapes.add_table(2, 2, 0, 0, 100, 100).table
    for (row, column), value in {
        (0, 0): "Curso",
        (0, 1): "Horas",
        (1, 0): "Primeros auxilios",
        (1, 1): "4",
    }.items():
        grid.cell(row, column).text = value

    hidden = presentation.slides.add_slide(presentation.slide_layouts[1])
    hidden.shapes.title.text = "Borrador oculto"
    hidden.element.set("show", "0")
    presentation.save(str(path))
    return path


def make_xlsx(path: Path, title: str | None = None) -> Path:
    workbook = Workbook()
    if title is not None:
        workbook.properties.title = title
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Dietas"
    sheet.append(["País", "Importe", "Desde", "Vigente", None])
    sheet.append(["España", 53.34, date(2026, 1, 1), True])
    sheet.append(["Francia", 70.0, None, False])
    sheet.append([None, None, None, None])
    sheet.append(["Portugal", None, None, None, "nota suelta"])
    hidden = workbook.create_sheet("Oculta")
    hidden.append(["Secreto"])
    hidden.sheet_state = "hidden"
    workbook.create_sheet("Vacía")
    workbook.save(path)
    return path


def make_odt(path: Path, title: str | None = None) -> Path:
    document = OpenDocumentText()
    if title is not None:
        document.meta.addElement(dc.Title(text=title))
    body = document.text
    body.addElement(text.P(text="Normas generales de la empresa."))
    body.addElement(text.H(outlinelevel=1, text="Teletrabajo"))
    body.addElement(text.P(text="Se puede teletrabajar dos días a la semana."))
    body.addElement(text.H(outlinelevel=2, text="Solicitud"))
    items = text.List()
    item = text.ListItem()
    item.addElement(text.P(text="La solicitud se hace en el portal del empleado."))
    items.addElement(item)
    body.addElement(items)
    grid = table.Table(name="Horario")
    for values in (["Día", "Horario"], ["Lunes", "8-15"]):
        row = table.TableRow()
        for value in values:
            cell = table.TableCell()
            cell.addElement(text.P(text=value))
            row.addElement(cell)
        grid.addElement(row)
    body.addElement(grid)
    document.save(str(path))
    return path


def make_odp(path: Path) -> Path:
    document = OpenDocumentPresentation()
    layout = style.PageLayout(name="Diseño")
    document.automaticstyles.addElement(layout)
    master = style.MasterPage(name="Predeterminada", pagelayoutname=layout)
    document.masterstyles.addElement(master)
    for name, heading, body in (
        ("p1", "Plan de formación", "Curso 2026"),
        ("p2", "Cursos obligatorios", "Prevención de riesgos laborales"),
    ):
        page = draw.Page(name=name, masterpagename=master)
        for kind, content in (("title", heading), ("outline", body)):
            frame = draw.Frame(width="20cm", height="3cm", x="1cm", y="1cm")
            frame.setAttrNS(PRESENTATIONNS, "class", kind)
            box = draw.TextBox()
            box.addElement(text.P(text=content))
            frame.addElement(box)
            page.addElement(frame)
        document.presentation.addElement(page)
    document.save(str(path))
    return path


def make_ods(path: Path) -> Path:
    document = OpenDocumentSpreadsheet()
    hidden_style = style.Style(name="oculta", family="table")
    hidden_style.addElement(style.TableProperties(display="false"))
    document.automaticstyles.addElement(hidden_style)

    def row(*values: str, repeated: int | None = None) -> table.TableRow:
        element = table.TableRow(numberrowsrepeated=repeated) if repeated else table.TableRow()
        for value in values:
            cell = table.TableCell()
            if value:
                cell.addElement(text.P(text=value))
            element.addElement(cell)
        # Como LibreOffice: celdas vacías repetidas hasta el final de la fila.
        element.addElement(table.TableCell(numbercolumnsrepeated=16000))
        return element

    sheet = table.Table(name="Dietas")
    sheet.addElement(row("País", "Importe"))
    sheet.addElement(row("España", "53,34"))
    sheet.addElement(row("Francia", "70"))
    # Como LibreOffice: un millón de filas vacías repetidas.
    sheet.addElement(row(repeated=1_048_000))
    document.spreadsheet.addElement(sheet)

    hidden = table.Table(name="Oculta", stylename=hidden_style)
    hidden.addElement(row("Secreto"))
    document.spreadsheet.addElement(hidden)
    document.save(str(path))
    return path
