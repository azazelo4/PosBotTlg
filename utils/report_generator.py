import io
import logging
from datetime import date, datetime, timedelta
import openpyxl # type: ignore
from database import generate_report, get_sales_summary_data, get_returns_summary_data, get_buybacks_summary_data

logger = logging.getLogger(__name__)

def generate_full_sales_report_excel(start_date: date, end_date: date):
    """
    Generates a sales report in Excel format with two sheets: Summary and Details.
    Returns a BytesIO object containing the Excel file.
    """
    wb = openpyxl.Workbook()

    # --- Summary Sheet ---
    summary_sheet = wb.active
    summary_sheet.title = "Сводка"

    summary_sheet.append(["Отчет за период:"])
    summary_sheet.append(["Начало:", start_date.strftime('%Y-%m-%d')])
    summary_sheet.append(["Конец:", end_date.strftime('%Y-%m-%d')])
    summary_sheet.append([]) # Empty row for spacing

    summary_data = get_sales_summary_data(start_date, end_date)

    if summary_data and summary_data['overall_summary']:
        overall = summary_data['overall_summary']
        summary_sheet.append(["Общая сводка продаж:"])
        summary_sheet.append(["Всего продаж:", overall['total_sales_count']])
        summary_sheet.append(["Общая сумма продаж:", f"{overall['total_sales_amount']:.2f}"])
        summary_sheet.append(["Средняя сумма продажи:", f"{overall['average_sale_price']:.2f}"])
        summary_sheet.append([])

        if summary_data['payment_type_breakdown']:
            summary_sheet.append(["Разбивка по типу оплаты:"])
            summary_sheet.append(["Тип оплаты", "Количество продаж", "Сумма продаж"])
            for row in summary_data['payment_type_breakdown']:
                summary_sheet.append([
                    row['payment_type'],
                    row['payment_type_sales_count'],
                    f"{row['payment_type_sales_amount']:.2f}"
                ])
        else:
            summary_sheet.append(["Нет данных по разбивке оплаты."])
    else:
        summary_sheet.append(["Нет сводных данных за выбранный период."])

    # --- Detailed Report Sheet ---
    detailed_sheet = wb.create_sheet("Детализация")
    headers = [
        'ID Продажи', 'Дата продажи', 'Сумма продажи', 'Тип расчета',
        'Артикул', 'Тип товара', 'Модель', 'Размер', 'Тип металла',
        'Вес', 'Цена за грамм', 'Цена товара (перв.)',
        'Производитель', 'Комментарии',
        'Продавец', 'Магазин товара'
    ]
    detailed_sheet.append(headers)

    report_data_list = generate_report(start_date, end_date)

    if not report_data_list:
        detailed_sheet.append(["Нет данных за выбранный период."])
    else:
        for row_tuple in report_data_list:
            processed_row = list(row_tuple)
            if len(processed_row) > 1 and isinstance(processed_row[1], (datetime, date)):
                processed_row[1] = processed_row[1].strftime('%Y-%m-%d %H:%M:%S')
            for i, cell_value in enumerate(processed_row):
                if hasattr(cell_value, 'as_tuple') and hasattr(cell_value, 'quantize'):
                    processed_row[i] = float(cell_value)
                elif not isinstance(cell_value, (str, int, float, bool, date, datetime)) and cell_value is not None:
                    processed_row[i] = str(cell_value)
                elif cell_value is None:
                    processed_row[i] = ""
            detailed_sheet.append(processed_row)

    report_buffer = io.BytesIO()
    wb.save(report_buffer)
    report_buffer.seek(0)
    return report_buffer