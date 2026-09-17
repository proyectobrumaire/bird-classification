FROM public.ecr.aws/lambda/python:3.11

# gcc necesario para compilar numpy 1.x (la imagen Lambda no lo trae)
RUN yum install -y gcc gcc-c++ && yum clean all

# numpy<2 usa setuptools clásico (solo necesita gcc, no Meson)
RUN pip install --no-cache-dir "numpy<2" Pillow tqdm boto3

# PyTorch CPU-only
RUN pip install --no-cache-dir \
    torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Código del proyecto
COPY bird_detector.py            ${LAMBDA_TASK_ROOT}/
COPY dl_main.py                  ${LAMBDA_TASK_ROOT}/
COPY predict_image_from_tensors.py ${LAMBDA_TASK_ROOT}/
COPY utils_bounding_boxes_separation.py ${LAMBDA_TASK_ROOT}/
COPY utils_crop_segmentation.py  ${LAMBDA_TASK_ROOT}/
COPY lambda_handler.py           ${LAMBDA_TASK_ROOT}/

CMD ["lambda_handler.handler"]
