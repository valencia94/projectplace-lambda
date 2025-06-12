################  Lambda Python 3.10  ################
FROM public.ecr.aws/lambda/python:3.10

# -------- 1. enable EPEL *without* amazon-linux-extras ----------
# (The rpm lives under /7/Everything/ ; pin a known working version)
RUN yum -y update && \
    curl -SL -o /tmp/epel.rpm \
         https://dl.fedoraproject.org/pub/epel/7/Everything/x86_64/Packages/e/epel-release-7-14.noarch.rpm && \
    rpm -ivh /tmp/epel.rpm && rm -f /tmp/epel.rpm

# -------- 2. install LibreOffice headless variant + a font ------
# --skip-broken lets the build survive if an optional lang-pack vanishes.
RUN yum -y install libreoffice \
                   libreoffice-core \
                   libreoffice-writer \
                   dejavu-sans-fonts \
    --setopt=skip_missing_names_on_install=False \
    --setopt=skip_broken=True && \
    yum clean all && rm -rf /var/cache/yum

# Good practice for LO inside Lambda (it writes to $HOME/.config)
ENV HOME=/tmp

# -------- 3. Python deps ---------------------------------------
COPY requirements.txt .
RUN pip3 install --no-cache-dir --upgrade pip \
 && pip3 install --no-cache-dir -r requirements.txt

# -------- 4. Your code & assets --------------------------------
COPY lambda_handler.py .
COPY logo/ ./logo/

# Keep AWS’s ENTRYPOINT; just set the handler
CMD ["lambda_handler.lambda_handler"]
