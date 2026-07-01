"""
LUỒNG DỮ LIỆU (DATA FLOW)
Khi Client gửi request DELETE /orders/{order_id} lên hệ thống, dữ liệu
đi theo 1 trong 4 luồng sau, nhưng LUÔN được chuẩn hóa về đúng 1 khuôn
JSON 6 trường trước khi trả về Client:

Luồng 1 - Dữ liệu request không hợp lệ (ví dụ order_id không phải số):
    Client request -> FastAPI parse path param thất bại -> FastAPI tự
    raise RequestValidationError -> Global Handler
    @app.exception_handler(RequestValidationError) bắt lấy -> đóng gói
    thành envelope với statusCode=422, error=chi tiết lỗi validate từ
    exc.errors() -> trả JSONResponse về Client.

Luồng 2 - Lỗi nghiệp vụ chủ động (order không tồn tại / đã DELIVERED):
    Client request -> vào hàm xử lý -> điều kiện nghiệp vụ sai ->
    code tự raise HTTPException(status_code, detail) -> Global Handler
    @app.exception_handler(HTTPException) bắt lấy -> đóng gói thành
    envelope với statusCode=exc.status_code, message/error=exc.detail
    -> trả JSONResponse về Client.

Luồng 3 - Lỗi hệ thống runtime không lường trước (crash, ép kiểu sai...):
    Client request -> vào hàm xử lý -> dữ liệu nội bộ bị lỗi (ví dụ
    field bị None thay vì string) khiến một lệnh Python bên trong ném
    ra Exception gốc (AttributeError, TypeError...) -> Global Handler
    @app.exception_handler(Exception) bắt lấy TRƯỚC KHI lỗi thô (kèm
    file, số dòng) lộ ra ngoài -> đóng gói thành envelope với
    statusCode=500, message thân thiện, error="Internal Server Error"
    (không chứa bất kỳ thông tin kỹ thuật thật nào) -> trả JSONResponse.

Luồng 4 - Xử lý thành công:
    Client request -> vào hàm xử lý -> mọi điều kiện hợp lệ -> cập nhật
    status thành "CANCELLED" -> hàm TỰ đóng gói kết quả vào cùng khuôn
    envelope (statusCode=200, data=order đã cập nhật, error=None) ->
    trả JSONResponse về Client.

=> Nhờ có hàm build_envelope() dùng chung cho cả 4 luồng, Frontend chỉ
cần viết DUY NHẤT một logic đọc response (luôn check field "error" có
null hay không) thay vì phải đoán cấu trúc khác nhau cho từng loại lỗi.

SẢN PHẨM HOÀN CHỈNH - CÁC TRƯỜNG HỢP NGHIỆP VỤ
Trường hợp                              | Kết quả mong muốn
------------------------------------------|----------------------------------
Đơn hàng tồn tại, đang PENDING           | statusCode=200, data=order với
                                          | status đã đổi thành "CANCELLED"
order_id không tồn tại                   | statusCode=404, error="Order not
                                          | found", data=None
Đơn hàng đã ở trạng thái DELIVERED       | statusCode=400, error="Không thể
                                          | hủy đơn hàng đã giao", data=None
order_id gửi lên không phải kiểu số      | statusCode=422 (RequestValidation),
                                          | data=None
Dữ liệu nội bộ bị lỗi gây crash runtime  | statusCode=500, error="Internal
                                          | Server Error", data=None, KHÔNG
                                          | lộ stack trace thật
"""

from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

app = FastAPI()

orders_db = [
    {"id": 1, "code": "SP001", "status": "PENDING"},
    {"id": 2, "code": "SP002", "status": "DELIVERED"},
    {"id": 3, "code": "SP003", "status": None},
]


def build_envelope(request: Request, status_code: int, message: str, data=None, error=None):
    return {
        "statusCode": status_code,
        "message": message,
        "data": data,
        "error": error,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "path": str(request.url.path),
    }


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    envelope = build_envelope(
        request,
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "Dữ liệu gửi lên không hợp lệ",
        data=None,
        error=exc.errors(),
    )
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=envelope)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    envelope = build_envelope(
        request, exc.status_code, exc.detail, data=None, error=exc.detail
    )
    return JSONResponse(status_code=exc.status_code, content=envelope)


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    envelope = build_envelope(
        request,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "Đã xảy ra lỗi hệ thống, vui lòng thử lại sau",
        data=None,
        error="Internal Server Error",
    )
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=envelope)


@app.delete("/orders/{order_id}")
def cancel_order(order_id: int, request: Request):
    order = next((o for o in orders_db if o["id"] == order_id), None)
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    current_status = order["status"].strip().upper()

    if current_status == "DELIVERED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không thể hủy đơn hàng đã giao",
        )

    order["status"] = "CANCELLED"

    envelope = build_envelope(
        request, status.HTTP_200_OK, "Hủy đơn hàng thành công", data=order, error=None
    )
    return JSONResponse(status_code=status.HTTP_200_OK, content=envelope)

