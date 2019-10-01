package com.netscape.cmstools.ca;

import org.apache.commons.cli.CommandLine;
import org.dogtagpki.cli.CLI;
import org.dogtagpki.util.logging.PKILogger;

import com.netscape.certsrv.ca.CACertClient;
import com.netscape.certsrv.cert.CertRequestInfo;
import com.netscape.certsrv.request.RequestId;
import com.netscape.cmstools.cli.MainCLI;

public class CACertRequestShowCLI extends CLI {

    public static org.slf4j.Logger logger = org.slf4j.LoggerFactory.getLogger(CACertRequestShowCLI.class);

    CACertCLI certCLI;

    public CACertRequestShowCLI(CACertCLI certCLI) {

        super("request-show", "Show certificate request", certCLI);
        this.certCLI = certCLI;
    }

    @Override
    public void printHelp() {
        formatter.printHelp(getFullName() + " <Request ID> [OPTIONS...]", options);
    }

    @Override
    public void execute(String[] args) throws Exception {

        CommandLine cmd = parser.parse(options, args);

        if (cmd.hasOption("help")) {
            printHelp();
            return;
        }

        if (cmd.hasOption("debug")) {
            PKILogger.setLevel(PKILogger.Level.DEBUG);

        } else if (cmd.hasOption("verbose")) {
            PKILogger.setLevel(PKILogger.Level.INFO);
        }

        String[] cmdArgs = cmd.getArgs();

        if (cmdArgs.length < 1) {
            throw new Exception("Missing Certificate Request ID.");
        }

        RequestId requestId = null;
        try {
            requestId = new RequestId(cmdArgs[0]);
        } catch (NumberFormatException e) {
            throw new Exception("Invalid certificate request ID " + cmdArgs[0] + ".", e);
        }

        MainCLI mainCLI = (MainCLI) getRoot();
        mainCLI.init();

        CACertClient certClient = certCLI.getCertClient();
        CertRequestInfo certRequest = certClient.getRequest(requestId);

        MainCLI.printMessage("Certificate request \"" + requestId + "\"");
        CACertCLI.printCertRequestInfo(certRequest);
    }
}
