FROM public.ecr.aws/lambda/python:3.10More actions

# 1) Install curl so we can fetch the EPEL .rpm
RUN yum -y install curl

# 2) Download a known EPEL 7 release RPM. 
#    (Amazon Linux 2 is mostly compatible with RHEL 7 packages.)
RUN curl -SL -o /tmp/epel.rpm \
    https://dl.fedoraproject.org/pub/epel/7/Everything/x86_64/Packages/e/epel-release-7-14.noarch.rpm

# 3) Install the EPEL repo from that .rpm
RUN rpm -ivh /tmp/epel.rpm || echo "Attempted to install EPEL"

# 4) Try installing LibreOffice in one of these packages.
#    If 'libreoffice-headless' doesn't exist, we fallback to 'libreoffice'
#    If that also fails, we fallback to 'libreoffice-core libreoffice-writer'More actions
RUN yum -y install libreoffice-headless || \
    yum -y install libreoffice || \
    yum -y install libreoffice-core libreoffice-writer || \
    echo "All LibreOffice installs failed. EPEL might not ship it."

# 5) Copy and install Python dependencies
COPY requirements.txt .
RUN pip3 install --upgrade pip && \
    pip3 install -r requirements.txt             # ✅ Works
    # (optionally add  --target "$LAMBDA_TASK_ROOT"
    #  to keep deps in /var/task, but not required)

# 6) Production handler + assets
COPY lambda_handler.py .
COPY logo/ ./logo/

# 7) Tag-aware handler (optional switch at build time)
COPY scripts/lambda_handler_tag.py ./tag.py

# 8) Promote tag.py → lambda_handler.py if requested
ARG USE_TAG_HANDLER=false
RUN if [ "$USE_TAG_HANDLER" = "true" ]; then \
        mv ./tag.py ./lambda_handler.py ; \
    fi

# 6) Lambda entry-point
CMD ["lambda_handler.lambda_handler"]

